import logging
from logging.handlers import RotatingFileHandler

def setup_logger():
    log_file = 'data/bot.log'
    api_log_file = 'data/api_responses.log'  # New file for API responses
    
    # Clear log files if they exist
    try:
        with open(log_file, 'w'):
            pass  # This clears the file contents
    except IOError as e:
        print(f"Warning: Could not clear log file {log_file} - {e}")

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # Set to lowest level for handlers to filter
    
    # Main logger formatter
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    # API response logger formatter (simpler format)
    api_formatter = logging.Formatter('%(asctime)s - %(message)s')
    
    # Main log file handler (as before)
    file_handler = RotatingFileHandler(
        log_file, 
        maxBytes=1*1024*1024,
        backupCount=1,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    
    # Console handler (as before)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)
    
    # New handler for API responses (separate file)
    api_handler = RotatingFileHandler(
        api_log_file,
        maxBytes=1*1024*1024,
        backupCount=3,
        encoding='utf-8'
    )
    api_handler.setFormatter(api_formatter)
    api_handler.setLevel(logging.INFO)  # We'll use INFO level for API responses
    api_handler.addFilter(lambda record: record.name == 'api_logger')  # Only log API responses
    
    # Create a separate logger for API responses
    api_logger = logging.getLogger('api_logger')
    api_logger.setLevel(logging.INFO)
    api_logger.addHandler(api_handler)
    api_logger.propagate = False  # Prevent propagation to root logger
    
    # Suppress verbose logs from libraries (as before)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("TeleBot").setLevel(logging.WARNING)
    
    return