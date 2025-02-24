import logging
import re
from os import environ

logging.basicConfig(level=environ.get("LOG_LEVEL", logging.INFO))
logging.getLogger(__name__)


def sanitize_field(field, data):
    """Sanitize field to standard format."""
    logging.debug("Sanitizing {}".format(field))
    result = {field: data}
    sanitize_regex_for = {
        "attendee": ((r"(?s)(\r)?\n( |\t)", ""),),
        "description": (
            (r"(?s)(\r)?\n( |\t)", ""),
            (r"(?s)((\\+\s*n\s*){1,}-\s*\\\s*n)?(\s*-)?\s*:\s*:\s*~.*$", ""),
            (r"&nbsp(\\)?;", " "),
            (r"&amp(\\)?;", "&"),
            (r"(\\){1,},", ","),
            (r"(\\){1,};", ";"),
            (r"(?s)((\\r)?\\n|\s){1,}$", ""),
        ),
        "dtend": ((r"^(\d{8})$", "\\1T000000Z"),),
        "dtstart": ((r"^(\d{8})$", "\\1T000000Z"),),
        "html": (
            (r"(?s)(\r)?\n( |\t)", ""),
            (r"(\\){1,}n", "<br>"),
            (r"(\\){1,},", ","),
            (r"(\\){1,};", ";"),
        ),
        "location": (
            (r"(?s)(\r)?\n( |\t)", ""),
            (r"(\\){1,},", ","),
            (r"(\\){1,};", ";"),
        ),
        "mailto": ((r"(?s)(\r)?\n( |\t)", ""), (r"prvs=[^=\s@]+=", "")),
        "name": (
            (r"\s*([^@]*)@(([\w\-\=]*\.){1,}(\w+))\s*", "\\1[at]\\2"),
            (r"(\\){1,},", ","),
            (r"(\\){1,};", ";"),
        ),
        "organizer": (
            (r"(?s)(\r)?\n( |\t)", ""),
            (r"\s*([^@]*)@(([\w\-\=]*\.){1,}(\w+))\s*", "\\1[at]\\2"),
            (r"(\\){1,},", ","),
            (r"(\\){1,};", ";"),
            (r"prvs=[^=\s@]+=", ""),
        ),
        "org_mailto": ((r"(?s)(\r)?\n( |\t)", ""), (r"prvs=[^=\s@]+=", "")),
        "original_uid": ((r"(?s)(\r)?\n( |\t)", ""),),
        "outlook_desktop": (
            (r"(?<!<\\\\n>)(\\\\n\\\\n)(?!\\\\n)", "\\\\\\\\n"),
            (r"(?<!<br>)(<br><br>)(?!<br>)", "<br>"),
        ),
        "partstat": ((r"(?s)(\r)?\n( |\t)", ""),),
        "return_path": ((r"^.*<([^\>])", "\\1"), (r"prvs=[^=\s@]+=", "")),
        "summary": (
            (r"(?s)(\r)?\n( |\t)", ""),
            (r"(\\){1,},", ","),
            (r"(\\){1,};", ";"),
            (r"(?s)((\\r)?\\n|\s){1,}$", ""),
        ),
    }

    if result[field] and sanitize_regex_for.get(field, None):
        for index, regex in enumerate(sanitize_regex_for[field]):
            result[field] = re.sub(
                regex[0], regex[1], result.get(field, "None")
            )
            logging.debug(
                "Value: {value}\nRegex: {regex}".format(
                    value=result[field],
                    regex={"index": str(index), "regex": regex},
                )
            )

    return result
