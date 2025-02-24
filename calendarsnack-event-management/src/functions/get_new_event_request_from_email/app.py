"""Process new event request from email."""

import json
import logging
import os

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
    format="%(message)s", level=os.environ.get("LOG_LEVEL", "WARNING")
)


def lambda_handler(event, _):
    """Handle lambda event."""
    logging.info(event)
    tasks = {}
    logging.info("Event:\n%s", event)

    for record in event["Records"]:
        try:
            process_request_from(record)
        except Exception as error:  # pylint: disable=broad-except
            logging.exception(error)
            tasks["failed"] = tasks.get("failed", 0) + 1

    return cleanup_all(tasks)


def process_request_from(record):
    """Process request from record."""
    event_request = extract_event_request_from(*get_email_from(record))
    process_event_request_by_method(event_request)
    delete_successfully_processed_sqs(record)


def extract_event_request_from(email, event_request_uid):
    """Extract event request from email."""
    event_request = Ical()
    event_request.read_ical_from(email, uid=event_request_uid)
    event_request.standardize_ical_fields()

    return event_request


def get_email_from(record):
    """Get email from record."""
    bucket, key = aws.get_s3_file_location_from(
        aws.get_sqs_message_with_s3_sns_notification_from(record)
    )

    return aws.get_s3_file_content_from(bucket, key, s3), get_uid_from_s3(key)


def get_uid_from_s3(key):
    """Event UID is the S3 key."""
    return key.split("/")[-1]


def process_event_request_by_method(event_request):
    """Process event request method."""
    method = {
        "cancel": cancel_event_for,
        "request": create_event_record_for,
        # "winmail": notify_user_of_invalid_winmail,
        "update": update_event_for,
    }

    if event_request_is_valid(event_request):
        method[event_request.ical["method"]](event_request.ical)
    else:
        notify_user_of_request_failure_for(event_request.ical)


def event_request_is_valid(event_request):
    """Validate event request."""
    approved_methods = ["cancel", "request", "update"]  # "winmail",

    return event_request.ical.get("method", "") in approved_methods


def cancel_event_for(event_request):
    """Cancel event."""
    return aws.publish_sns_message(
        message=json.dumps({"default": json.dumps(event_request)}),
        arn=os.environ["EVENT_CANCELLATION"],
        sns=sns,
    )


def create_event_record_for(event_request):
    """Create event record."""
    return aws.publish_sns_message(
        message=json.dumps({"default": json.dumps(event_request)}),
        arn=os.environ["NEW_EVENT_REQUEST"],
        sns=sns,
    )


# def notify_user_of_invalid_winmail(event_request):
#     """Notify user of invalid winmail."""
#     return aws.publish_sns_message(
#         message=json.dumps(
#             {"default": json.dumps({"mailto": event_request["return_path"]})}
#         ),
#         arn=os.environ["INVALID_WINMAIL_EVENT"],
#         sns=sns,
#     )


def update_event_for(event_request):
    """Update event."""
    return aws.publish_sns_message(
        message=json.dumps({"default": json.dumps(event_request)}),
        arn=os.environ["EVENT_UPDATE"],
        sns=sns,
    )


def notify_user_of_request_failure_for(event_request):
    """Notify user of event request failure."""
    return aws.publish_sns_message(
        message=json.dumps(
            {"default": json.dumps({"mailto": event_request["return_path"]})}
        ),
        arn=os.environ["FAILED_EVENT_CREATE"],
        sns=sns,
    )


def delete_successfully_processed_sqs(message):
    """Delete successfully processed sqs messages."""
    if valid_sqs_message_from(message):
        # Removes SQS message from queue to prevent retry
        clean_up_successful_task(message["receiptHandle"])


def valid_sqs_message_from(request):
    """Validate sqs message."""
    return request.get("receiptHandle", False)


def clean_up_successful_task(message_id):
    """Clean up successful tasks from queue."""
    return aws.delete_sqs_message(
        message_id, url=os.environ["SQS_URL"], sqs=sqs
    )


def cleanup_all(tasks):
    """Validate if any tasks failed."""
    if tasks.get("failed", False):
        # Forces SQS retry of failed events
        raise Exception("{} tasks failed".format(tasks["failed"]))

    return "Complete"
