import logging
import sys
import os
from logging.handlers import RotatingFileHandler

def setup_logger():
    """ Logging Module with File Rotation """

    logger = logging.getLogger("SQL Agent Logs")
    logger.setLevel(logging.INFO)    
    if not logger.handlers:
        log_dir = "logs"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        formatter = logging.Formatter(
            '%(asctime)s - [%(levelname)s] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        log_file_path = os.path.join(log_dir, "log_file.log")
        file_handler = RotatingFileHandler(
            log_file_path,
            maxBytes=24 * 1024 * 1024, 
            backupCount=10,       
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    return logger

# Initialize logger 
logger = setup_logger()