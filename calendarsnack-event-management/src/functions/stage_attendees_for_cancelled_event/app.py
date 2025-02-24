"""Stage attendees for cancelled event."""

import json
import logging
import os

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
sns = boto3.client("sns", region_name=os.environ["REGION"])
sqs = boto3.client("sqs")


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
    queue_cancellation_for_processing(
        request, get_attendee_list(request["uid"])
    )
    delete_successfully_processed_sqs(record)


def get_attendee_list(uid):
    """Get attendee list."""
    return dynamodb.query(
        TableName=os.environ["DYNAMODB_TABLE"],
        KeyConditionExpression="pk = :pk AND begins_with ( sk , :attendee )",
        ProjectionExpression=(
            "attendee, mailto, #name, origin, prodid, #status"
        ),
        ExpressionAttributeNames={"#name": "name", "#status": "status"},
        ExpressionAttributeValues={
            ":pk": {"S": "event#{}".format(uid)},
            ":attendee": {"S": "attendee#"},
        },
        ConsistentRead=False,
    ).get("Items", [])


def queue_cancellation_for_processing(event, attendees):
    """Queue cancellation for processing."""
    for attendee in attendees:
        ical = {
            "email": attendee["attendee"]["S"],
            "name": attendee.get("name", {}).get("S", "customer"),
        }

        ical.update(event)

        aws.publish_sns_message(
            message=json.dumps({"default": json.dumps(ical)}),
            arn=os.environ["EVENT_CANCELLATION_REQUEST"],
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


def clean_up_successful_task(task_id):
    """Clean up successfully completed tasks."""
    return aws.delete_sqs_message(task_id, url=os.environ["SQS_URL"], sqs=sqs)


def cleanup_all(tasks):
    """Validate all tasks were cleaned up."""
    if tasks.get("failed", False):
        # Forces SQS retry of failed events
        raise Exception("{} tasks failed".format(tasks["failed"]))

    return "Complete"
