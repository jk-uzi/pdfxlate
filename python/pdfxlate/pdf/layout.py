"""DocLayout-YOLO ONNX layout detection.

Ported from the upstream project, with the babeldoc asset lookup replaced by
:mod:`pdfxlate.assets` so the ONNX model is fetched directly from HuggingFace.

The model is a YOLO detector that labels each region of a page (title, plain
text, figure, table, formula, ...).  The pipeline turns its boxes into an
integer mask per page; a mask value of ``0`` means "preserve as-is" and any
value ``>= 2`` groups characters into a translatable paragraph.
"""

from __future__ import annotations

import abc
import ast
import logging
import os

import cv2
import numpy as np

from pdfxlate import assets

try:
    import onnx
    import onnxruntime
except ImportError as e:  # pragma: no cover - platform specific
    if "DLL load failed" in str(e):
        raise OSError(
            "Microsoft Visual C++ Redistributable is not installed. "
            "Download it at https://aka.ms/vs/17/release/vc_redist.x64.exe"
        ) from e
    raise

logger = logging.getLogger(__name__)

_BACKEND_PROVIDERS = {
    "cpu": ["CPUExecutionProvider"],
    "cuda": ["CUDAExecutionProvider", "CPUExecutionProvider"],
    "dml": ["DmlExecutionProvider", "CPUExecutionProvider"],
}

_preferred_backend: str | None = None

# ONNX Runtime's CPU memory arena caches allocations between inference calls.
# That is a speed optimisation whose cost is resident memory which never
# shrinks: measured on this project's DocLayout model, three consecutive runs
# grow the process by ~235 MB with the arena on versus ~6 MB with it off, and
# the peak drops from ~400 MB to ~174 MB (about 227 MB saved).  In exchange,
# one page takes about 1.1 s instead of 0.6 s.  Off is the default so a
# finished job does not leave that memory pinned; a machine with memory to
# spare can switch it back on through ``layout.cpu_mem_arena``.
_memory_arena: bool = False


def set_backend(name: str) -> None:
    """Set the ONNX Runtime execution provider backend.

    Args:
        name: One of ``auto``, ``cpu``, ``cuda``, ``dml``.
    """
    global _preferred_backend
    _preferred_backend = None if name == "auto" else name


def set_memory_arena(enabled: bool) -> None:
    """Enable or disable ONNX Runtime's CPU memory arena.

    Args:
        enabled: ``True`` trades roughly 227 MB of resident memory for about
            half the per-page inference time (measured on the DocLayout
            model).  Applies to models constructed after the call.
    """
    global _memory_arena
    _memory_arena = enabled


class DocLayoutModel(abc.ABC):
    @staticmethod
    def load_onnx():
        return OnnxModel.from_pretrained()

    @staticmethod
    def load_available():
        return DocLayoutModel.load_onnx()

    @property
    @abc.abstractmethod
    def stride(self) -> int:
        """Stride of the model input."""

    @abc.abstractmethod
    def predict(self, image, imgsz=1024, **kwargs) -> list:
        """Detect layout regions on one rendered page image."""


class YoloResult:
    """Detection results for a page, sorted by confidence."""

    def __init__(self, boxes, names):
        self.boxes = [YoloBox(data=d) for d in boxes]
        self.boxes.sort(key=lambda x: x.conf, reverse=True)
        self.names = names


class YoloBox:
    def __init__(self, data):
        self.xyxy = data[:4]
        self.conf = data[-2]
        self.cls = data[-1]


class OnnxModel(DocLayoutModel):
    def __init__(self, model_path: str):
        model_path = str(model_path)
        self.model_path = model_path

        # Read metadata without deserializing the full graph; the 75 MB model
        # would otherwise be held twice at peak.
        model = onnx.load(model_path, load_external_data=False)
        metadata = {d.key: d.value for d in model.metadata_props}
        self._stride = ast.literal_eval(metadata["stride"])
        self._names = ast.literal_eval(metadata["names"])
        del model

        sess_options = onnxruntime.SessionOptions()
        # See _memory_arena: the arena is memory that outlives the run.
        sess_options.enable_cpu_mem_arena = _memory_arena
        sess_options.graph_optimization_level = (
            onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        )

        if _preferred_backend and _preferred_backend in _BACKEND_PROVIDERS:
            providers = _BACKEND_PROVIDERS[_preferred_backend]
        else:
            providers = onnxruntime.get_available_providers()

        # CoreML/TensorRT emit compiled nodes that cannot be serialized, so the
        # optimized graph is only cached for providers that round-trip safely.
        compiled_providers = {"CoreMLExecutionProvider", "TensorrtExecutionProvider"}
        can_cache = not compiled_providers.intersection(providers)
        if can_cache:
            optimized_path = model_path + ".optimized"
            if os.path.exists(optimized_path):
                model_path = optimized_path
            else:
                sess_options.optimized_model_filepath = optimized_path

        self.model = onnxruntime.InferenceSession(
            model_path, sess_options, providers=providers
        )
        logger.info("ONNX Runtime providers: %s", self.model.get_providers())

    @staticmethod
    def from_pretrained():
        return OnnxModel(assets.ensure_layout_model())

    @property
    def stride(self):
        return self._stride

    def resize_and_pad_image(self, image, new_shape):
        """Resize keeping aspect ratio and pad to a multiple of the stride."""
        if isinstance(new_shape, int):
            new_shape = (new_shape, new_shape)

        h, w = image.shape[:2]
        new_h, new_w = new_shape
        r = min(new_h / h, new_w / w)
        resized_h, resized_w = int(round(h * r)), int(round(w * r))

        image = cv2.resize(image, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)

        pad_w = (new_w - resized_w) % self.stride
        pad_h = (new_h - resized_h) % self.stride
        top, bottom = pad_h // 2, pad_h - pad_h // 2
        left, right = pad_w // 2, pad_w - pad_w // 2

        return cv2.copyMakeBorder(
            image, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114)
        )

    def scale_boxes(self, img1_shape, boxes, img0_shape):
        """Map boxes from the padded model frame back to original page pixels."""
        gain = min(img1_shape[0] / img0_shape[0], img1_shape[1] / img0_shape[1])
        pad_x = round((img1_shape[1] - img0_shape[1] * gain) / 2 - 0.1)
        pad_y = round((img1_shape[0] - img0_shape[0] * gain) / 2 - 0.1)
        boxes[..., :4] = (boxes[..., :4] - [pad_x, pad_y, pad_x, pad_y]) / gain
        return boxes

    def predict(self, image, imgsz=1024, **kwargs):
        orig_h, orig_w = image.shape[:2]
        pix = self.resize_and_pad_image(image, new_shape=imgsz)
        pix = np.transpose(pix, (2, 0, 1))  # CHW
        pix = np.expand_dims(pix, axis=0)  # BCHW
        pix = pix.astype(np.float32) / 255.0
        new_h, new_w = pix.shape[2:]

        preds = self.model.run(None, {"images": pix})[0]

        preds = preds[preds[..., 4] > 0.25]
        preds[..., :4] = self.scale_boxes(
            (new_h, new_w), preds[..., :4], (orig_h, orig_w)
        )
        return [YoloResult(boxes=preds, names=self._names)]


class ModelInstance:
    """Process-wide cached model, loaded once and reused across jobs."""

    value: OnnxModel = None
