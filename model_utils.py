import os
import urllib.request
import logging
import cv2

logger = logging.getLogger(__name__)

MODEL_DIR_ENV = "MODEL_DIR"

MOBILENET_PROTOTXT = "MobileNetSSD_deploy.prototxt"
MOBILENET_CAFFEMODEL = "MobileNetSSD_deploy.caffemodel"

PROTO_URL = "https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/MobileNetSSD_deploy.prototxt"
MODEL_URL = "https://github.com/chuanqi305/MobileNet-SSD/raw/master/MobileNetSSD_deploy.caffemodel"

def get_model_dir(default_dir):
    return os.environ.get(MODEL_DIR_ENV, os.path.join(default_dir, "models"))

def ensure_mobilenet(default_dir):
    d = get_model_dir(default_dir)
    os.makedirs(d, exist_ok=True)
    proto = os.path.join(d, MOBILENET_PROTOTXT)
    model = os.path.join(d, MOBILENET_CAFFEMODEL)

    if not os.path.exists(proto):
        try:
            logger.info(f"Downloading prototxt to {proto}")
            urllib.request.urlretrieve(PROTO_URL, proto)
        except Exception as e:
            logger.error("Failed to download prototxt: %s", e)

    if not os.path.exists(model):
        try:
            logger.info(f"Downloading caffemodel to {model}")
            urllib.request.urlretrieve(MODEL_URL, model)
        except Exception as e:
            logger.error("Failed to download caffemodel: %s", e)

    # load net if available
    net = None
    try:
        if os.path.exists(proto) and os.path.exists(model):
            net = cv2.dnn.readNetFromCaffe(proto, model)
    except Exception as e:
        logger.error("Failed to load MobileNet SSD net: %s", e)

    return net
