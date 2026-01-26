
# Core/logging_utils.py
import logging, os, sys

def setup_logging(level=logging.INFO, app_name="TrueEditor"):
    logger = logging.getLogger("trueeditor")
    logger.setLevel(level)
    logger.handlers.clear()

    log_dir = os.path.join(os.path.expanduser("~"), app_name, "logs")
    os.makedirs(log_dir, exist_ok=True)

    fh = logging.FileHandler(os.path.join(log_dir, f"{app_name.lower()}.log"), encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))
    logger.addHandler(fh)

    # Dev/console runs only
    if getattr(sys, "stderr", None) is not None:
        sh = logging.StreamHandler(stream=sys.stderr)
        sh.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logger.addHandler(sh)

    # GUI exe: ensure stdout/stderr exist
    if getattr(sys, "frozen", False):
        if getattr(sys, "stdout", None) is None:
            sys.stdout = open(os.path.join(log_dir, "stdout.log"), "a", buffering=1, encoding="utf-8")
        if getattr(sys, "stderr", None) is None:
            sys.stderr = open(os.path.join(log_dir, "stderr.log"), "a", buffering=1, encoding="utf-8")
    return logger
