import logging
import re
import uuid
from datetime import datetime
from os import environ
from urllib.request import urlopen

from thirtyone.extract import (extract_field, extract_fields_by_method,
                               extract_ical_from)
from thirtyone.sanitize import sanitize_field

logging.basicConfig(level=environ.get("LOG_LEVEL", logging.INFO))
logging.getLogger(__name__)


class Ical:
    """31Events logic to ingest and build new ical data."""

    def __init__(self):
        self.ical = {}

    def __repr__(self):
        """Print ical attributes as a string according to method."""
        if (
            self.ical.get("method", None) == "REQUEST"
            or self.ical.get("method", None) == "PUBLISH"
        ):
            mandatory_ical_fields = [
                "method",
                "uid",
                "original_uid",
                "description",
                "description_html",
                "dtstamp",
                "dtstart",
                "dtend",
                "location",
                "location_html",
                "mailto",
                "organizer",
                "prodid",
                "sequence",
                "status",
                "summary",
                "summary_html",
            ]
        elif self.ical.get("method", None) == "REPLY":
            mandatory_ical_fields = [
                "method",
                "uid",
                "name",
                "mailto_rsvp",
                "partstat",
                "prodid",
                "return_path",
            ]
        elif self.ical.get("method", None) == "CANCEL":
            mandatory_ical_fields = [
                "mailto",
                "method",
                "organizer",
                "original_uid",
                "prodid",
                "return_path",
            ]
        else:
            mandatory_ical_fields = ["method", "return_path"]

        return self.print_ical_attributes_string(mandatory_ical_fields)

    def print_ical_attributes_string(self, fields):
        """Build ical attribute string with specified attributes"""
        class_string_representation = ""

        for field in fields:
            class_string_representation += "{field}: {value}\n".format(
                field=field.upper(), value=self.ical.get(field, None)
            )

        return class_string_representation

    def build_ical_from(
        self,
        description="",
        dtend=None,
        dtstamp=datetime.strftime(datetime.now(), "%Y%m%dT%H%M%SZ"),
        dtstart=None,
        location="",
        organizer="",
        mailto=None,
        method="REQUEST",
        recipient=None,
        rsvp_email=None,
        sequence=0,
        status="CONFIRMED",
        summary="",
        transp="OPAQUE",
        uid=None,
    ):
        """Build REQUEST ical."""
        logging.debug("Building ical")
        ical = (
            "BEGIN:VCALENDAR\r\n"
            + "PRODID:-//31Events//CalendarSnack//EN\r\n"
            + "VERSION:2.0\r\n"
            + "METHOD:{method}\r\n"
            + "BEGIN:VEVENT\r\n"
            + "{summary}\r\n"
            + "{description}\r\n"
            + "CLASS:PUBLIC\r\n"
            + "DTSTART;TZID=Etc/GMT:{dtstart}\r\n"
            + "DTEND;TZID=Etc/GMT:{dtend}\r\n"
            + "{location}\r\n"
            + "PRIORITY:0\r\n"
            + "SEQUENCE:{sequence}\r\n"
            + "STATUS:{status}\r\n"
            + "{uid}\r\n"
            + "DTSTAMP:{dtstamp}\r\n"
            + "{recipient}\r\n"
            + "{organizer}\r\n"
            + "{mailto}\r\n"
            + "TRANSP:{transp}\r\n"
            + "STATUS:{status}\r\n"
            + "X-THIRTYONE-USER-STATUS:FREE\r\n"
            + "X-THIRTYONE-EVENT-STATUS:FREE\r\n"
            + "BEGIN:VALARM\r\n"
            + "ACTION:DISPLAY\r\n"
            + "{tz_description}\r\n"
            + "TRIGGER;RELATED=START:-PT15M\r\n"
            + "END:VALARM\r\n"
            + "END:VEVENT\r\n"
            + "BEGIN:VTIMEZONE\r\n"
            + "TZID:Etc/GMT\r\n"
            + "TZURL:http://tzurl.org/zoneinfo/Etc/GMT\r\n"
            + "X-LIC-LOCATION:Etc/GMT\r\n"
            + "BEGIN:STANDARD\r\n"
            + "TZOFFSETFROM:+0000\r\n"
            + "TZOFFSETTO:+0000\r\n"
            + "TZNAME:GMT\r\n"
            + "DTSTART:16010101T000000\r\n"
            + "RDATE:16010101T000000\r\n"
            + "END:STANDARD\r\n"
            + "END:VTIMEZONE\r\n"
            + "END:VCALENDAR"
        ).format(
            description=self.format_text_length("DESCRIPTION:" + description),
            dtend=re.sub("Z", "", dtend),
            dtstamp=dtstamp,
            dtstart=re.sub("Z", "", dtstart),
            location=self.format_text_length("LOCATION:" + location),
            mailto=self.format_text_length("X-YAHOO-YID:" + mailto),
            method=method,
            organizer=self.format_text_length(
                "ORGANIZER;CN="
                + organizer
                + ";"
                + 'SENT-BY="'
                + "mailto:"
                + rsvp_email
                + '":'
                + "mailto:"
                + rsvp_email
            ),
            recipient=self.generate_attendee_field(recipient),
            sequence=str(sequence),
            status=status,
            summary=self.format_text_length("SUMMARY:" + summary),
            transp=transp,
            tz_description=self.format_text_length("DESCRIPTION:" + summary),
            uid=self.format_text_length("UID:" + uid),
        )

        return ical

    def format_text_length(self, text):
        """Format text length."""
        logging.debug("Enforcing character boundary limits")

        line_boundary = 70
        visible_character_length = line_boundary - 5
        visible_character_boundary = line_boundary - 4
        final_text = ""

        if len(text) <= line_boundary:
            final_text = text
        else:
            final_text = "{initial_text}\r\n".format(
                initial_text=text[:visible_character_length]
            )
            text = text[visible_character_length:]

            while len(text) / visible_character_boundary > 1:
                final_text += " {append_text}\r\n".format(
                    append_text=text[:visible_character_boundary]
                )
                text = text[visible_character_boundary:]
            final_text += " {append_text}".format(append_text=text[0:])

        return final_text

    def generate_attendee_field(self, email):
        """Generate attendee field."""
        logging.debug("Generating attendee line for ical")

        attendee = (
            "ATTENDEE;"
            + "PARTSTAT=NEEDS-ACTION;"
            + "ROLE=REQ_PARTICIPANT;"
            + "RSVP=TRUE;"
            + "SCHEDULE-STATUS=1.1:"
            + "mailto:"
            + email
        )

        return self.format_text_length(attendee)

    def read_ical_from(
        self, text, from_email=True, uid=str(uuid.uuid4().hex)[:16]
    ):
        """Read iCal."""
        logging.debug("Reading ical: {}".format(uid))
        self.ical["uid"] = uid

        if from_email:
            self.ical.update(self.get_return_path_from(text))
            ical_header = extract_field("ical_header", data=text)
            ical = extract_ical_from(text, header=ical_header)
        else:
            ical = text

        if ical:
            if "<icalurl" in ical.lower():
                logging.debug("Shared calendar detected")
                ical = self.get_shared_calendar_from(ical)

            if self.get_method_from(ical) != "WINMAIL":
                self.get_ical_fields(
                    ical, sender=self.ical.get("return_path", "")
                )

        return self.ical

    def get_return_path_from(self, email):
        """Get return path from email."""
        logging.debug("Extracting return_path from email")
        return_path = extract_field("return_path", data=email)["return_path"]

        return sanitize_field("return_path", data=return_path)

    def get_shared_calendar_from(
        self, attachment
    ):  # pragma: no cover (R0201: Method could be a function (no-self-use))
        """Get shared calendar from attachment."""
        logging.debug("Downloading ics")
        download = extract_field("icalUrl", data=attachment)

        return urlopen(download["icalUrl"]).read().decode("utf8") #nosec (temp fix, see https://stackoverflow.com/questions/48779202/audit-url-open-for-permitted-schemes-allowing-use-of-file-or-custom-schemes)

    def get_method_from(self, ical):
        """Get method from iCal."""
        logging.debug("Extracting METHOD from ical")
        self.ical.update(extract_field("method", data=ical))

        return self.ical.get("method", None)

    def get_ical_fields(self, ical, sender):
        """Get common iCal fields."""
        logging.debug("Extracting relevant ical fields from ical")

        if self.ical["method"] == "PUBLISH":
            self.ical["method"] = "REQUEST"

        results = extract_fields_by_method(
            self.ical["method"], data=ical, sender=sender
        )

        if results:
            self.ical.update(results)

        return self.ical

    def standardize_ical_fields(self):
        """Standardize ical fields."""
        fields_to_lowercase = [
            "mailto",
            "method",
            "original_uid",
            "prodid",
            "return_path",
            "status",
            "uid",
        ]

        for field in fields_to_lowercase:
            if self.ical.get(field, None):
                self.ical[field] = self.ical[field].lower()
