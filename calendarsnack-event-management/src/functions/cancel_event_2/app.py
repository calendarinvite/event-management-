"""SAM Application for cancelling events."""

import json
import logging
import os
from datetime import datetime

import boto3
from thirtyone import aws

# Logging Configuration
root = logging.getLogger()
if root.handlers:
    for handler in root.handlers:
        root.removeHandler(handler)
logging.basicConfig(
    format="%(message)s", level=os.environ.get("LOG_LEVEL", logging.WARNING)
)  # pylint: disable=C0209


# Variable Reuse
dynamodb = boto3.client("dynamodb", region_name=os.environ["REGION"])
dynamodb_table = os.environ["DYNAMODB_TABLE"]
sns = boto3.client("sns")
sqs = boto3.client("sqs")


def lambda_handler(event, _):
    """Handle lambda."""
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
    original_event = get_event_uid_from(request["original_uid"])
    event = get_event_from(original_event["uid"])

    if event_is_not_cancelled(event):
        event.update(cancel_event(event))
        send_notification_of_cancelled(stage_cancelled(event))

    delete_successfully_processed_sqs(record)


def get_event_uid_from(uid):
    """Get event user id from user id."""
    return aws.get_dynamodb_record_for(
        "original_event#{}".format(uid),
        secondary_key="original_event#{}".format(uid),
        dynamodb=dynamodb,
        dynamodb_table=dynamodb_table,
    )


def get_event_from(uid):
    """Get event from user id."""
    return aws.get_dynamodb_record_for(
        "event#{}".format(uid),
        secondary_key="event#{}".format(uid),
        dynamodb=dynamodb,
        dynamodb_table=dynamodb_table,
    )


def event_is_not_cancelled(event):
    """Check if event is not cancelled."""
    return event["status"] != "cancelled"


def cancel_event(event):
    """Cancel event."""
    timestamp = int(datetime.utcnow().timestamp())
    event["method"] = "cancel"
    event["status"] = "cancelled"
    event["dtstamp"] = timestamp
    event["last_modified"] = timestamp
    event["sequence"] += 1

    return dynamodb.update_item(
        TableName=os.environ["DYNAMODB_TABLE"],
        Key={
            "pk": {"S": "event#{}".format(event["uid"])},
            "sk": {"S": "event#{}".format(event["uid"])},
        },
        UpdateExpression=(
            "SET #dtstamp = :timestamp,"
            + "#last_modified = :timestamp,"
            + "#method = :method,"
            + "#status = :status,"
            + "#sequence = :sequence"
        ),
        ExpressionAttributeNames={
            "#dtstamp": "dtstamp",
            "#last_modified": "last_modified",
            "#method": "method",
            "#status": "status",
            "#sequence": "sequence",
        },
        ExpressionAttributeValues={
            ":timestamp": {"N": str(timestamp)},
            ":method": {"S": event["method"]},
            ":status": {"S": event["status"]},
            ":sequence": {"N": str(event["sequence"])},
        },
    )


def stage_cancelled(event):
    """Stage cancelled event."""
    staged_event = {}

    cancellation_fields = [
        "description",
        "dtend",
        "dtstamp",
        "dtstart",
        "last_modified",
        "location",
        "status",
        "mailto",
        "method",
        "original_organizer",
        "organizer",
        "sequence",
        "summary",
        "uid",
    ]

    for field in cancellation_fields:
        staged_event[field] = event.get(field, "")

    return staged_event


def send_notification_of_cancelled(event):
    """Send notification of cancelled event."""
    return aws.publish_sns_message(
        message=json.dumps({"default": json.dumps(event)}),
        arn=os.environ["EVENT_CANCELLED"],
        sns=sns,
    )


def delete_successfully_processed_sqs(message):
    """Delete successfully processed sqs message."""
    if valid_sqs_message_from(message):
        # Removes SQS message from queue to prevent retry
        clean_up_successful_task(message["receiptHandle"])


def valid_sqs_message_from(request):
    """Validate sqs message from request."""
    return request.get("receiptHandle", False)


def clean_up_successful_task(identification):
    """Clean up successful tasks."""
    return aws.delete_sqs_message(
        identification, url=os.environ["SQS_URL"], sqs=sqs
    )


def cleanup_all(tasks):
    """Clean up all tasks."""
    if tasks.get("failed", False):
        # Forces SQS retry of failed events
        raise Exception("{} tasks failed".format(tasks["failed"]))

    return "Complete"
