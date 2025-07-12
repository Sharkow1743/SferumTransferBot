import json
import logging

logger = logging.getLogger()

def load(key):
    try:
        with open("data.json", "r") as f:
            data = json.load(f)
            return data.get(key, None)
    except FileNotFoundError:
        logger.warning("data.json not found")
        return None
    except json.JSONDecodeError:
        logger.error("Error decoding JSON in data.json")
        return None
    except Exception as e:
        logger.error(f"Error reading key '{key}' - {type(e).__name__}: {str(e)}")
        logger.debug("Full error details:", exc_info=True)
        return None

def save(key, value):
    try:
        data = {}
        try:
            with open("data.json", "r") as f:
                data = json.load(f)
        except FileNotFoundError:
            pass

        data[key] = value

        with open("data.json", "w") as f:
            json.dump(data, f)
        logger.debug(f"Saved key '{key}' with value: {value}")
    except Exception as e:
        logger.error(f"Error saving key '{key}' - {type(e).__name__}: {str(e)}")
        logger.debug("Full error details:", exc_info=True)