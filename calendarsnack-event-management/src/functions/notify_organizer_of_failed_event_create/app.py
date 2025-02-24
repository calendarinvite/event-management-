"""Notify organizer of failed event create."""

import logging
from os import environ

import boto3
from thirtyone import aws


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

    cleanup_all(tasks)


def process_request_from(record):
    """Notify organizer of failed event create."""
    request = aws.get_sqs_message_with_sns_notification_from(record)
    event_notification_html = get_event_notification_template()

    aws.send_ses_standard_email(
        ses=ses,
        subject=environ["SUBJECT"],
        sender=environ["SENDER"],
        recipient=request["mailto"],
        html=event_notification_html,
    )

    delete_successfully_processed_sqs(record)


def get_event_notification_template():
    """Get event notification template."""
    event_notification_template = aws.get_codecommit_file_for(
        environ["FAILED_EVENT_NOTIFICATION_EMAIL"],
        repository=environ["CODECOMMIT_REPO"],
        codecommit=codecommit,
    )

    return event_notification_template


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


def configure_logging(log_level="WARNING"):
    """Configure program logging."""
    root = logging.getLogger()

    _ = [
        root.removeHandler(handler)
        for handler in root.handlers
        if root.handlers
    ]

    logging.basicConfig(**get_logging_settings(log_level))


def get_logging_settings(log_level):
    """Configure logging settings."""
    return {
        "format": "%(asctime)s - %(levelname)s - %(funcName)s(): %(message)s",
        "datefmt": "[%Y.%m.%d] %H:%M:%S",
        "level": log_level,
    }


# Variable Re-use
codecommit = boto3.client("codecommit")
ses = boto3.client("ses")
sqs = boto3.client("sqs")
configure_logging(environ.get("LOG_LEVEL", "WARNING"))
