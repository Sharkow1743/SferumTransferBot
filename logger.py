import logging
from logging.handlers import RotatingFileHandler
import sys

# Custom Formatter to exclude tracebacks for the console
class ConsoleFormatterWithNoTraceback(logging.Formatter):
    """
    A custom formatter that formats log records for the console.
    For exception records, it formats a single line with the error message
    instead of a multi-line traceback.
    """
    def format(self, record):
        # Store the original exception info, as we will modify the record
        original_exc_info = record.exc_info
        original_exc_text = record.exc_text

        # Temporarily clear exception info so the base class doesn't format it
        record.exc_info = None
        record.exc_text = None

        # Let the base class format the main part of the message
        formatted_message = super().format(record)

        # If there was an exception, append our custom one-line summary
        if original_exc_info:
            # original_exc_info is a tuple (type, value, traceback)
            exception_type, exception_value, _ = original_exc_info
            formatted_message += f": {exception_type.__name__}: {exception_value}"

        # Restore the original exception info for any other handlers
        record.exc_info = original_exc_info
        record.exc_text = original_exc_text

        return formatted_message

def setup_logger():
    log_file = 'bot.log'
    api_log_file = 'api_responses.log'

    try:
        with open(log_file, 'w'):
            pass
    except IOError as e:
        print(f"Warning: Could not clear log file - {e}")

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # --- Formatters ---
    file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    console_formatter = ConsoleFormatterWithNoTraceback('%(asctime)s - %(levelname)s - %(message)s')
    api_formatter = logging.Formatter('%(asctime)s - %(message)s')

    # --- Handlers ---

    # Main log file handler (logs everything, including API logs)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=1*1024*1024,
        backupCount=1,
        encoding='utf-8'
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.INFO)
    # *** KEY CHANGE HERE ***
    # Add a filter to prevent 'api_logger' messages from reaching the console.
    # This filter allows a record to pass only if its name is NOT 'api_logger'.
    console_handler.addFilter(lambda record: record.name != 'api_logger')
    logger.addHandler(console_handler)

    # API response handler (separate file)
    api_handler = RotatingFileHandler(
        api_log_file,
        maxBytes=1*1024*1024,
        backupCount=3,
        encoding='utf-8'
    )
    api_handler.setFormatter(api_formatter)
    api_handler.setLevel(logging.INFO)
    # This filter ensures ONLY logs from 'api_logger' go to this file
    api_handler.addFilter(lambda record: record.name == 'api_logger')
    logger.addHandler(api_handler)

    # Get a specific logger instance for API calls
    api_logger = logging.getLogger('api_logger')

    # Suppress verbose logs from libraries
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("TeleBot").setLevel(logging.WARNING)

    return logger, api_logger