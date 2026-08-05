import os
import logging
import cv2

logger = logging.getLogger(__name__)

MODEL_DIR_ENV = "MODEL_DIR"

MOBILENET_PROTOTXT = "MobileNetSSD_deploy.prototxt"
MOBILENET_CAFFEMODEL = "MobileNetSSD_deploy.caffemodel"

def get_model_dir(default_dir):
    return os.environ.get(MODEL_DIR_ENV, os.path.join(default_dir, "models"))

def ensure_mobilenet(default_dir):
    d = get_model_dir(default_dir)
    os.makedirs(d, exist_ok=True)
    proto = os.path.join(d, MOBILENET_PROTOTXT)
    model = os.path.join(d, MOBILENET_CAFFEMODEL)

    # Production startup must be deterministic: do not download executable
    # model artifacts from the network. Provision and checksum-pin them during
    # installation, or run safely in motion-only mode.
    if not (os.path.exists(proto) and os.path.exists(model)):
        logger.warning("MobileNet-SSD files are not provisioned in %s; using motion-only alerts", d)
        return None

    # load net if available
    net = None
    try:
        if os.path.exists(proto) and os.path.exists(model):
            net = cv2.dnn.readNetFromCaffe(proto, model)
    except Exception as e:
        logger.error("Failed to load MobileNet SSD net: %s", e)

    return net
