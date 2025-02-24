"""Notify organizer of event limit reached."""

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

    return cleanup_all(tasks)


def process_request_from(record):
    """Send email notification to organizer that event limit is reached."""
    event = aws.get_sqs_message_with_sns_notification_from(record)
    event_notification_email = get_event_notification_email_template()

    aws.send_ses_standard_email(
        ses=ses,
        subject=environ["SUBJECT"],
        sender=environ["SENDER"],
        recipient=event["mailto"],
        html=event_notification_email,
    )

    delete_successfully_processed_sqs(record)


def get_event_notification_email_template():
    """Retrieve email template from CodeCommit."""
    return aws.get_codecommit_file_for(
        environ["EVENT_LIMIT_REACHED_NOTIFICATION_EMAIL"],
        repository=environ["CODECOMMIT_REPO"],
        codecommit=codecommit,
    )


def delete_successfully_processed_sqs(message):
    """Delete successfully processed sqs messages."""
    if valid_sqs_message_from(message):
        # Removes SQS message from queue to prevent retry
        clean_up_successful_task(message["receiptHandle"])


def valid_sqs_message_from(request):
    """Validate sqs message in request."""
    return request.get("receiptHandle", False)


def clean_up_successful_task(task_id):
    """Clean up successfully completed tasks."""
    return aws.delete_sqs_message(task_id, url=environ["SQS_URL"], sqs=sqs)


def cleanup_all(tasks):
    """Validate all tasks are cleaned up."""
    if tasks.get("failed", False):
        # Forces SQS retry of failed events
        raise Exception("{} tasks failed".format(tasks["failed"]))

    return "Complete"


def configure_logging(level="WARNING"):
    """Set up logging."""
    root = logging.getLogger()

    if root.handlers:
        for handler in root.handlers:
            root.removeHandler(handler)

    logging.basicConfig(format="%(message)s", level=level)


# Variable Reuse
codecommit = boto3.client("codecommit")
ses = boto3.client("ses")
sqs = boto3.client("sqs")
configure_logging(environ.get("LOG_LEVEL", "WARNING"))
