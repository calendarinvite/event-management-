"""Process new event reply from email."""

import json
import logging
import os
from datetime import datetime

import boto3
from thirtyone import aws
from thirtyone.ical import Ical

# Variable Reuse
s3 = boto3.client("s3")
sns = boto3.client("sns")
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
    """Handle lambda event."""
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
    """Process record request."""
    event_reply = extract_event_request_from(*get_email_from(record))

    if event_reply.ical.get("method", False):
        send_notification_of_new(event_reply.ical)

    delete_successfully_processed_sqs(record)


def extract_event_request_from(email, event_request_uid):
    """Extract event request from email."""
    event_request = Ical()
    event_request.read_ical_from(email, uid=event_request_uid)

    return event_request


def get_email_from(record):
    """Extract email from record."""
    bucket, key = aws.get_s3_file_location_from(
        aws.get_sqs_message_with_s3_sns_notification_from(record)
    )

    return aws.get_s3_file_content_from(bucket, key, s3), get_uid_from_s3(key)


def get_uid_from_s3(key):
    """Event UID is the S3 key."""
    return key.split("/")[-1]


def send_notification_of_new(event_reply):
    """Send notification of new event reply."""
    return aws.publish_sns_message(
        message=json.dumps(
            {"default": json.dumps(format_reply_request_from(event_reply))}
        ),
        arn=os.environ["NEW_EVENT_REPLY"],
        sns=sns,
    )


def format_reply_request_from(event_reply):
    """Format event reply request."""
    return {
        "attendee": event_reply["mailto_rsvp"].lower(),
        "dtstamp": get_epoch_from(event_reply["dtstamp"]),
        "partstat": event_reply["partstat"].lower(),
        "prodid": event_reply["prodid"].lower(),
        "uid": event_reply["uid"].lower(),
    }


def get_epoch_from(timestamp):
    """Convert timestamp to epoch."""
    return int(datetime.strptime(timestamp, "%Y%m%dT%H%M%SZ").timestamp())


def delete_successfully_processed_sqs(message):
    """Delete successfully processed sqs message."""
    if valid_sqs_message_from(message):
        # Removes SQS message from queue to prevent retry
        clean_up_successful_task(message["receiptHandle"])


def valid_sqs_message_from(request):
    """Validate sqs message from request."""
    return request.get("receiptHandle", False)


def clean_up_successful_task(task_id):
    """Clean up successful tasks."""
    return aws.delete_sqs_message(task_id, url=os.environ["SQS_URL"], sqs=sqs)


def cleanup_all(tasks):
    """Clean up all tasks."""
    if tasks.get("failed", False):
        # Forces SQS retry of failed events
        raise Exception("{} tasks failed".format(tasks["failed"]))

    return "Complete"
