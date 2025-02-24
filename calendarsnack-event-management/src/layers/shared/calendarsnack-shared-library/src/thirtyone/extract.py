import logging
import re
from base64 import b64decode
from datetime import datetime
from os import environ
from quopri import decodestring

from thirtyone.sanitize import sanitize_field
from thirtyone.timezone import convert_time_to_utc

logging.basicConfig(level=environ.get("LOG_LEVEL", logging.INFO))
logging.getLogger(__name__)


def extract_ical_from(email, header):
    """Extracts ical from email."""
    logging.debug("Extracting ical from email")
    if header.get("content_type", None) == "application/ms-tnef":
        ical = "METHOD:WINMAIL"
    elif header:
        encoding = extract_field("encoding", data=header.get("settings", ""))
        ical = extract_payload_from(email, encoding.get("type", "base64"))
    else:
        logging.debug("Ical not detected")
        ical = None

    return ical


def extract_payload_from(email, encoding):
    """Extracting payload from email."""
    logging.debug("Extracting {} payload".format(encoding))
    payload = extract_field("ical_payload", data=email)

    if encoding.lower() == "base64":
        ical = decode_base64(payload.get("content", ""))
    elif encoding.lower() == "quoted-printable":
        ical = decode_quoted_printable(payload.get("content", ""))
    else:
        ical = payload.get("content", "")

    return ical


def decode_base64(payload):
    """Decodes base64 content."""
    logging.debug("Decoding base64 payload")

    return b64decode(payload).decode("utf-8-sig")


def decode_quoted_printable(payload):
    """Decodes 7-bit, 8-bit, and quoted-printable payloads."""
    logging.debug("Decoding quoted-printable payload")
    return decodestring(payload).decode("utf-8-sig")


def extract_fields_by_method(method, data, sender):
    """Extracts specific fields based on ical method."""
    logging.debug("Extracting ical based on method")
    extract_by_method = {
        "CANCEL": extract_ical_cancel_fields,
        "REPLY": extract_ical_reply_fields,
        "REQUEST": extract_ical_request_fields,
    }

    if method == "PUBLISH":
        method = "REQUEST"

    extract_ical_fields_from = extract_by_method[method]

    return extract_ical_fields_from(data, sender)


def extract_ical_cancel_fields(ical, sender):
    """Extracts relevant ical CANCEL fields."""
    results = {"mailto": sender.lower(), "status": "CANCELLED"}

    cancel_fields = ("organizer", "original_uid", "prodid")

    for field in cancel_fields:
        results.update(extract_field(field, data=ical))
        if results.get(field, None):
            results.update(
                sanitize_field(field, data=results.pop(field, None))
            )

    if results.get("organizer", None) is None:
        results["organizer"] = sanitize_field(
            field="organizer", data=results["mailto"]
        )["organizer"]

    return results


def extract_ical_reply_fields(ical, sender):
    """Extracts relevant ical REPLY fields."""
    results = {"dtstamp": datetime.strftime(datetime.now(), "%Y%m%dT%H%M%SZ")}

    reply_fields = ("attendee", "method", "prodid", "uid")

    for field in reply_fields:
        results.update(extract_field(field, data=ical))
        if results.get(field, None):
            results.update(
                sanitize_field(field, data=results.pop(field, None))
            )

    return enrich_ical_reply_fields(results)


def enrich_ical_reply_fields(fields):
    """Enriches extracted ical fields."""
    results = {}
    results.update(fields)
    results.update(extract_field(field="name", data=fields["attendee"]))

    results.update(extract_field("mailto_rsvp", data=fields["attendee"]))

    if results.get("name", None) is None:
        results["name"] = results["mailto_rsvp"]

    if "@" in results["name"]:
        results["name"] = sanitize_field(
            field="organizer", data=results["name"]
        )["organizer"]

    results.update(extract_field("partstat", data=results.pop("attendee")))

    return results


def extract_ical_request_fields(ical, sender):
    """Extracts relevant ical REQUEST fields."""
    results = initialize_ical_request_fields(sender)

    request_fields = (
        "description",
        "dtend",
        "dtstart",
        "location",
        "org_mailto",
        "original_uid",
        "prodid",
        "summary",
        "tzid",
    )

    for field in request_fields:
        results.update(extract_field(field, data=ical))
        if results.get(field, None):
            results.update(
                sanitize_field(field, data=results.pop(field, None))
            )

    results.update(enrich_ical_request_fields(results, ical))

    return results


def initialize_ical_request_fields(
    mailto,
):
    """Assigns default values to ical REQUEST."""
    current_time = datetime.strftime(datetime.now(), "%Y%m%dT%H%M%SZ")

    icalFields = {
        "created": current_time,
        "dtstamp": current_time,
        "last_modified": current_time,
        "mailto": mailto.lower(),
        "sequence": 0,
        "status": "CONFIRMED",
    }

    return icalFields


def enrich_ical_request_fields(fields, ical):
    """Enriches extracted ical fields."""
    if fields["mailto"] == "":
        fields.update(extract_field("mailto", data=fields["org_mailto"]))

    fields.update(
        extract_organizer_from(
            fields.pop("org_mailto", "None"), defaultValue=fields["mailto"]
        )
    )

    if fields.get(
        "description", None
    ) is not None and "Microsoft Corporation//Outlook" in fields.get(
        "description", None
    ):
        fields["description"] = sanitize_field(
            field="outlook_desktop", data=fields["description"]
        )

    if fields.get("description", None):
        _description = extract_field("description", data=ical)

        google_meet = extract_field(
            "google_meet",
            data=re.sub(
                r"(\r)?\n( )+", "", _description.pop("description", "")
            ),
        )

        if google_meet:
            # Append google meeting information to description, then sanitize

            fields["description"] += "\\\\n\\\\nJoin: {}".format(
                google_meet.pop("google_meet")
            )

            fields.update(
                sanitize_field(
                    "description", data=fields.pop("description", "")
                )
            )

    for field in "description location summary".split(" "):
        fields.update(convert_html_from(field, data=fields.get(field, None)))

    if fields.get("tzid", None):
        fields.update(
            convert_time_to_utc(
                time={
                    "dtend": fields.get("dtend", fields["dtstart"]),
                    "dtstart": fields["dtstart"],
                },
                timezone=fields.pop("tzid"),
            )
        )

    return fields


def extract_organizer_from(data, defaultValue):
    """Extracts organizer or assigns a default value."""
    logging.debug("Extracting organizer")
    result = {}
    result.update(extract_field("organizer", data=data))

    if result.get("organizer", None) is None:
        result["organizer"] = defaultValue

    return sanitize_field("organizer", result["organizer"])


def convert_html_from(field, data):
    """Transforms data to HTML."""
    logging.debug("Extracting HTML from {}".format(field))
    result = {
        field + "_html": sanitize_field("html", data=data).get("html", None)
    }

    return result


def extract_field(field, data):
    """Extracts field from data with regex."""
    logging.debug(
        "Extracting {field} from: {data}".format(
            field=field, data=str(data)[:78]
        )
    )
    result = {}
    extractions = {
        "attendee": (
            "(?s)ATTENDEE;"
            + "(?P<attendee>[^\r\n]+(((\r)?\n"
            + "( |\t)[^\r\n]+){1,})?)",
            r"(?s)(?P<attendee>PARTSTAT=\w+;"
            + "ROLE=[^\r\n]+(((\r)?\n( |\t)[^\r\n]+){1,})?)",
        ),
        "description": (
            "(?s)(?<!VALARM\r\n)"
            + "DESCRIPTION(;LANGUAGE=[^:]+)?:"
            + "(?P<description>[^\r\n]+(((\r)?\n"
            + "( |\t)[^\r\n]+){1,})?)",
        ),
        "dtend": ("(?s)BEGIN:VEVENT.*DTEND(;[^:]+)?:(?P<dtend>2[0-9TZ]+)",),
        "dtstamp": (
            "(?s)BEGIN:VEVENT.*DTSTAMP(;[^:]+)?:(?P<dtstamp>2[0-9TZ]+)",
        ),
        # Extractions ordered intentionally to avoid timezone extraction
        "dtstart": (
            "(?s)BEGIN:VEVENT.*DTSTART(;[^:]+)?:(?P<dtstart>2[0-9TZ]+)",
        ),
        "encoding": (
            r"(C|c)ontent-(T|t)ransfer-(E|e)ncoding: (?P<type>[\w\-]+)",
        ),
        "google_meet": (
            r"(?i)Join:\s*(?P<google_meet>https://meet.google.com\/[^\s\\\\]+)",
        ),
        # Extractions ordered intentionally to bias base64 extraction
        "ical_header": (
            r"(?s)Content\-(t|T)ype:\s+"
            + "(?P<content_type>application\/ics)"
            + '(?P<settings>([\w ;:=\-"\.\!\@\#\$\%\^\&\*'
            + "\(\)\_\+\,\<\>\?\/]+(\\r)?\\n(\\t)?){1,})",
            r"(?s)Content\-(t|T)ype:\s+"
            + "(?P<content_type>(text\/calendar|"
            + "application\/ms-tnef|application\/x-sharing-metadata-xml))"
            + '(?P<settings>([\w ;:=\-"\.\!\@\#\$\%\^\&\*'
            + "\(\)\_\+\,\<\>\?\/]+(\\r)?\\n(\\t)?){1,})",
        ),
        "ical_payload": (
            r"(?s)(C|c)ontent\-(T|t)ype:\s+"
            + "(application\/ics)"
            + '([\w ;:=\-"\.\!\@\#\$\%\^\&\*\(\)\_\+\,\<\>\?\/]+'
            + "(\r)?\n(\t)?){1,}(\r)?\n"
            + "(?P<content>BEGIN:VCALENDAR.*END:VCALENDAR|"
            + "([\w=\_\/+]+)(((\r)?\n[\w=\_\/+]+){1,})?)",
            "(?s)(?P<content>BEGIN:VCALENDAR.*END:VCALENDAR)",
            r"(?s)(C|c)ontent\-(T|t)ype:\s+"
            + "(application\/ics|text\/calendar|application\/ms-tnef|"
            + "application\/x-sharing-metadata-xml)"
            + '([\w ;:=\-"\.\!\@\#\$\%\^\&\*\(\)\_\+\,\<\>\?\/]+'
            + "(\\r)?\\n(\\t)?){1,}(\\r)?\\n"
            + "(?P<content>([\w=\_\/+]+)(((\\r)?\\n[\w=\_\/+]+){1,})?)",
        ),
        "ical_url": (r"(?i)\<IcalUrl[^\>]*\>(?P<ical_url>.+)\<\/IcalUrl",),
        "location": (
            "(?s)LOCATION(;LANGUAGE=[^:]+)?:"
            + "(?P<location>[^\r\n]+(((\r)?\n( |\t)[^\r\n]+){1,})?)",
        ),
        "mailto": ('(?i)mailto:(?P<mailto>[^:;"@]+@[^:@]+)$',),
        "mailto_rsvp": ('(?i)mailto:(?P<mailto_rsvp>[^:;"@]+@[^:@]+)$',),
        "method": (r"METHOD(:|=)\s*(?P<method>[A-Z]+)",),
        "name": (
            r"(?i)(CN|EMAIL)=(?P<name>[^;:]+)?.*:"
            + "mailto:[^@]+@([^\.]+\.){1,}\w+",
        ),
        "organizer": (
            r"(?i)^((?P<organizer>[^:;]*):)?"
            + "mailto:[^@]+@([^\.]+\.){1,}[a-zA-Z0-9]+",
            r"(?i)(?P<organizer>.*[^:;])?;(SENT-BY|EMAIL)=.*:"
            + "mailto:[^@]+@([^\.]+\.){1,}[a-zA-Z0-9]+",
        ),
        "org_mailto": (
            "(?s)ORGANIZER(;CN=|:)"
            + "(?P<org_mailto>[^:;\r\n]+(((\r)?\n( |\t)[^:;\r\n]+){1,})?)",
            "ORGANIZER(;CN=|:)(?P<org_mailto>[^:;\\\\]+)",
        ),
        "original_uid": (
            "(?s)UID(;LANGUAGE=[^:]+)?:"
            + "(?P<original_uid>[^\r\n]+(((\r)?\n( |\t)[^\r\n]+){1,})?)",
        ),
        "partstat": ("PARTSTAT=(?P<partstat>[A-Z]+)",),
        "prodid": (
            "(?s)PRODID:(?P<prodid>[^\r\n]+(((\r)?\n( |\t)[^\r\n]+){1,})?)",
        ),
        # Extractions ordered intentionally to avoid bounce emails
        "return_path": (
            r"(?mi)^Reply-To:\s+([^@]+\s+(<)?)?(?P<return_path>[^@\s]+@[^\s>]+)(>)?$",
            "envelope-from=(?P<return_path>[^;]+)",
            r"From: ([^\<]+\<)?(?P<return_path>[^\<\>\r\n]+)",
            r"^Return-Path: \<(?P<return_path>[^\>]+)",
        ),
        "status": ("STATUS:(?P<status>[A-Z]+)",),
        "summary": (
            "(?s)SUMMARY(;LANGUAGE=[^:]+)?:"
            + "(?P<summary>[^\r\n]+(((\r)?\n( |\t)[^\r\n]+){1,})?)",
        ),
        "tzid": ("TZID:(?P<tzid>[^\r\n]+)",),
        "uid": (
            "(?s)UID([^:]+)?:"
            + "(?P<uid>[^\r\n]+(((\r)?\n( |\t)[^\r\n]+){1,})?)",
        ),
    }

    for index, regex in enumerate(extractions[field]):
        extracted = re.search(regex, data)

        if extracted:
            result = extracted.groupdict()
            logging.debug(
                "Value: {value}\n{regex}".format(
                    value=result, regex={"index": str(index), "regex": regex}
                )
            )
            break

    return result
