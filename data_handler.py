import json
import logging

logger = logging.getLogger()

cache = {}

def load(key, file = "data.json"):
    if key in cache:
        return cache[key]
    try:
        with open(file, "r") as f:
            data = json.load(f)
            return data.get(key, None)
    except FileNotFoundError:
        logger.warning(f"{file} not found")
        return None
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON in {file}")
        return None
    except Exception as e:
        logger.error(f"Error reading key '{key}' - {type(e).__name__}: {str(e)}")
        logger.debug("Full error details:", exc_info=True)
        return None

def save(key, value, file = "data.json"):
    try:
        data = {}
        try:
            with open(file, "r") as f:
                data = json.load(f)
        except FileNotFoundError:
            pass

        data[key] = value

        with open(file, "w") as f:
            json.dump(data, f)
        cache[key] = value
        logger.debug(f"Saved key '{key}' with value: {value}")
    except Exception as e:
        logger.error(f"Error saving key '{key}' - {type(e).__name__}: {str(e)}")
        logger.debug("Full error details:", exc_info=True)