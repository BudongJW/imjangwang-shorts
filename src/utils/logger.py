"""파이프라인 로깅 설정."""

import logging
import sys
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent.parent / "output" / "logs"


def setup_logger(name: str = "pipeline", level: int = logging.INFO) -> logging.Logger:
    """파이프라인 로거를 설정한다.

    - 콘솔: 간결한 포맷
    - 파일: 상세 포맷 (output/logs/ 디렉토리)
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)

    # 콘솔 핸들러
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))

    # 파일 핸들러
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(
        LOG_DIR / "pipeline.log", encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger
