"""Notify organizer of successful event create."""

import logging
from os import environ

import boto3
from thirtyone import aws

# Variable Reuse
codecommit = boto3.client("codecommit")
ses = boto3.client("ses")
sqs = boto3.client("sqs")


# Logging Configuration
root = logging.getLogger()
if root.handlers:
    for handler in root.handlers:
        root.removeHandler(handler)
logging.basicConfig(
    format="%(message)s", level=environ.get("LOG_LEVEL", logging.WARNING)
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
    """Notify organizer of successful event create."""
    event = aws.get_sqs_message_with_sns_notification_from(record)
    event_notification_email = get_event_notification_email_template(event)

    aws.send_ses_standard_email(
        ses=ses,
        subject=environ["SUBJECT"],
        sender=environ["SENDER"],
        recipient=event["mailto"],
        html=event_notification_email,
    )

    delete_successfully_processed_sqs(record)


def get_event_notification_email_template(event):
    """Get event notification template."""
    # Removing paid logic
    # if event.get('paid', False):
    #     email_template = environ['NEW_PAID_EVENT_NOTIFICATION_EMAIL']
    # else:
    # email_template = environ['NEW_EVENT_NOTIFICATION_EMAIL']
    email_template = environ["NEW_EVENT_NOTIFICATION_EMAIL"]

    event_notification_template = aws.get_codecommit_file_for(
        email_template,
        repository=environ["CODECOMMIT_REPO"],
        codecommit=codecommit,
    )

    return (
        event_notification_template.replace("{mailto}", event["organizer"])
        .replace("{summary}", event["summary"])
        .replace("{uid}", event["uid"])
        .replace("{events}", str(event.get("events", 1)))
    )


def delete_successfully_processed_sqs(message):
    """Delete successfully processed sqs messages."""
    if valid_sqs_message_from(message):
        # Removes SQS message from queue to prevent retry
        clean_up_successful_task(message["receiptHandle"])


def valid_sqs_message_from(request):
    """Validate sqs message from request."""
    return request.get("receiptHandle", False)


def clean_up_successful_task(task_id):
    """Clean up successfully completed tasks."""
    return aws.delete_sqs_message(task_id, url=environ["SQS_URL"], sqs=sqs)


def cleanup_all(tasks):
    """Validate all tasks were completed."""
    if tasks.get("failed", False):
        # Forces SQS retry of failed events
        raise Exception("{} tasks failed".format(tasks["failed"]))

    return "Complete"
