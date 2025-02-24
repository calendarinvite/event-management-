"""Send event cancellation."""

import logging
import os
from datetime import datetime

import boto3
from thirtyone import aws
from thirtyone.ical import Ical

# Variable Reuse
dynamodb = boto3.client("dynamodb")
dynamodb_table = os.environ["DYNAMODB_TABLE"]
ses = boto3.client("ses", region_name=os.environ["REGION"])
sqs = boto3.client("sqs")


# Logging Configuration
root = logging.getLogger()
if root.handlers:
    for handler in root.handlers:
        root.removeHandler(handler)
logging.basicConfig(
    format="%(message)s", level=os.environ.get("LOG_LEVEL", logging.WARNING)
)  # pylint: disable=C0209


def lambda_handler(event, _):
    """Process API request."""
    logging.info(event)
    tasks = {}

    for record in event["Records"]:
        try:
            process_request_from(record)
        except Exception as error:  # pylint: disable=broad-except
            logging.exception(error)
            tasks["failed"] = tasks.get("failed", 0) + 1

    return cleanup_all(tasks)


def process_request_from(record):
    """Process request from record."""
    request = aws.get_sqs_message_with_sns_notification_from(record)
    send_cancellation(request)
    delete_successfully_processed_sqs(record)


def send_cancellation(request):
    """Send cancellation to attendee."""
    return aws.send_ses_invite_to_attendee(
        ical=get_ical(request),
        method="CANCEL",
        subject=f'CalendarSnack Event Cancelled: {request["summary"]}',
        sender=os.environ["SENDER"].format(
            request.get("organizer", "CalendarSnack Cancellation")
        ),
        recipient=request["email"],
        html=(
            "Event has been cancelled:"
            f'<br><br>{request.get("description_html", "")}'
        ),
        text=f'Event has been cancelled:\n\n{request.get("description", "")}',
        ses=ses,
    )


def get_ical(request):
    """Compile formatted ical for invite."""
    ical = get_ical_fields(request)
    ical["rsvp_email"] = os.environ["RSVP_EMAIL"]
    ical["recipient"] = request["email"]

    return Ical().build_ical_from(**ical)


def get_ical_fields(request):
    """Get ical fields from event."""
    ical = {
        "method": "CANCEL",
        "status": "CANCELLED",
        "organizer": request["organizer"],
        "mailto": "rsvp@calendarsnack.com",
    }

    ical_fields = (
        "description",
        "dtend",
        "dtstart",
        "location",
        "sequence",
        "summary",
        "uid",
    )

    for field in ical_fields:
        ical.update(validate_ical(field, request.get(field)))

    return ical


def validate_ical(field, value):
    """Validate ical field."""
    time_formats = ("dtend", "dtstart")

    if value:
        if field in time_formats:
            value = datetime.fromtimestamp(value).strftime("%Y%m%dT%H%M%SZ")

    return {field: value} if value else {}


def delete_successfully_processed_sqs(message):
    """Delete successfully processed sqs messages."""
    if valid_sqs_message_from(message):
        # Removes SQS message from queue to prevent retry
        clean_up_successful_task(message["receiptHandle"])


def valid_sqs_message_from(request):
    """Validate sqs message."""
    return request.get("receiptHandle", False)


def clean_up_successful_task(task_id):
    """Clean up successfully completed tasks."""
    return aws.delete_sqs_message(task_id, url=os.environ["SQS_URL"], sqs=sqs)


def cleanup_all(tasks):
    """Validate all tasks were cleaned up."""
    if tasks.get("failed", False):
        # Forces SQS retry of failed events
        raise Exception("{} tasks failed".format(tasks["failed"]))

    return "Complete"
