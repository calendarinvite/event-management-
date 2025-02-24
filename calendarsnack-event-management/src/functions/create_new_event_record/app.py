"""Create new event record."""

import json
import logging
from datetime import datetime
from os import environ

import boto3
from botocore.exceptions import ClientError
from thirtyone import aws


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
    """Process new event creation request."""
    event = aws.get_sqs_message_with_sns_notification_from(record)
    organizer = get_organizer_details(event)

    if organizer_can_send(organizer):
        staged_event = stage_request_from(event)
        create_new_event_record(event, staged_event, organizer)

    delete_successfully_processed_sqs(record)


def organizer_can_send(organizer):
    """Validate organizer authorization to send."""
    if (
        organizer["email"]
        and not account_is_suspended(organizer["email"])
        and events_available(organizer)
    ):
        authorized_to_send = True
    else:
        logging.warning("%s not authorized to send", organizer["email"])
        authorized_to_send = False

    return authorized_to_send


def get_organizer_details(request):
    """Return organizer email, statistics and subscription status."""
    organizer_email = request.get("mailto", None)

    return {
        "email": organizer_email,
        "paid": is_paid_account(organizer_email),
        "statistics": get_organizer_statistics(organizer_email),
    }


def account_is_suspended(organizer_email):
    """Check if account is suspended."""
    suspended_account = aws.get_dynamodb_record_for(
        f"organizer#{organizer_email}",
        secondary_key=f"suspended#{organizer_email}",
        dynamodb=dynamodb,
        dynamodb_table=dynamodb_table,
    )

    if suspended_account:
        logging.info("Account is suspended")

    return suspended_account


def is_paid_account(organizer_email):
    """Verify organizer has a paid subscription."""
    if organizer_email:
        subscription = aws.get_dynamodb_record_for(
            f"organizer#{organizer_email}",
            secondary_key=f"subscription#{organizer_email}",
            dynamodb=dynamodb,
            dynamodb_table=dynamodb_table,
        )
    else:
        subscription = None

    return subscription


def get_organizer_statistics(organizer_email):
    """Get organizer sending statistics."""
    if organizer_email:
        statistics = aws.get_dynamodb_record_for(
            f"organizer#{organizer_email}",
            secondary_key=f"organizer_statistics#{organizer_email}",
            dynamodb=dynamodb,
            dynamodb_table=dynamodb_table,
        )
    else:
        statistics = None

    return statistics


def events_available(organizer):
    """Check if organizer has available events to send."""
    if not organizer["statistics"]:
        logging.info("New organizer: %s", organizer["email"])
        create_organizer_statistics_record(organizer["email"])
        # organizer_can_send = True
    # DISABLING LIMITER
    # elif organizer['paid']:
    #     organizer_can_send = True
    # elif events_not_throttled(organizer):
    #     organizer_can_send = True
    # else:
    #     organizer_can_send = False

    # return organizer_can_send
    return True


def create_organizer_statistics_record(organizer):
    """Create organizer statistics record if it doesn't exist."""
    try:
        dynamodb.put_item(
            TableName=environ["DYNAMODB_TABLE"],
            Item={
                "pk": {"S": "organizer#{}".format(organizer)},
                "sk": {"S": "organizer_statistics#{}".format(organizer)},
                "events": {"N": "0"},
                "attendees": {"N": "0"},
                "origin": {"M": {}},
                "prodid": {"M": {}},
                "rsvp": {
                    "M": {
                        "accepted": {"N": "0"},
                        "declined": {"N": "0"},
                        "noaction": {"N": "0"},
                        "tentative": {"N": "0"},
                    }
                },
            },
            ConditionExpression=(
                "attribute_not_exists(pk) AND attribute_not_exists(sk)"
            ),
            ReturnConsumedCapacity="NONE",
        )
    except ClientError as error:
        if (
            error.response["Error"]["Code"]
            == "ConditionalCheckFailedException"
        ):
            logging.warning("Organzizer statistics record already exists")
        else:
            raise


def events_not_throttled(organizer):
    """Validate that unpaid account has not exceeded event throttle limit."""
    # organizer['email'], organizer['statistics']
    send_limit = int(environ.get("EVENT_LIMIT", 5))
    total_events = int(organizer["statistics"].get("events", 1))

    if not organizer["paid"]:
        throttled = total_events >= send_limit
    else:
        throttled = False

    if throttled:
        send_notification_of_event_limit_reached(organizer["email"])

    return not throttled


def send_notification_of_event_limit_reached(organizer_email):
    """Notify organizer of send limit reached."""
    return aws.publish_sns_message(
        message=json.dumps(
            {
                "default": json.dumps(
                    {
                        "mailto": organizer_email,
                        "send_limit": environ.get("EVENT_LIMIT", "5"),
                    }
                )
            }
        ),
        arn=environ["EVENT_LIMIT_REACHED"],
        sns=sns,
    )


def stage_request_from(event):
    """Format event request for DynamoDB."""
    staged_request = {}
    event["pk"] = event["sk"] = "event#{}".format(event["uid"])
    event["created"] = get_date_epoch(event.pop("created"))
    event["dtstamp"] = get_date_epoch(event.pop("dtstamp"))
    event["dtstart"] = get_date_epoch(event.pop("dtstart"))
    event["dtend"] = get_date_epoch(event.pop("dtend"))
    event["last_modified"] = get_date_epoch(event.pop("last_modified"))
    event["invite_limit"] = int(environ["EVENT_INVITE_LIMIT"])
    event["invite_limit_notification"] = False
    event["original_organizer"] = event["organizer"]
    event["tenant"] = event.get("tenant", "thirtyone")

    for field in event.keys():
        staged_request.update(
            format_dictionary_for_dynamodb(field, value=event.get(field, ""))
        )

    return staged_request


def format_dictionary_for_dynamodb(field, value=""):
    """Add additional data type layer to dictionary."""
    return {field: {get_dynamodb_format_for(value): str(value)}}


def get_dynamodb_format_for(value):
    """Map data type to DynamoDB format."""
    dynamodb_formats = {"<class 'int'>": "N", "<class 'str'>": "S"}

    return dynamodb_formats.get(str(type(value)).lower(), "S")


def get_date_epoch(timestamp):
    """Transform date string to epoch."""
    return int(datetime.strptime(timestamp, "%Y%m%dT%H%M%SZ").timestamp())


def create_new_event_record(event, staged_event, organizer):
    """Create new event record."""
    try:
        response = dynamodb.transact_write_items(
            TransactItems=[
                {
                    # Creates lookup for original_uid to uid
                    "Put": {
                        "TableName": environ["DYNAMODB_TABLE"],
                        "Item": get_original_event_record_from(staged_event),
                        "ConditionExpression": (
                            "attribute_not_exists(pk) and "
                            + "attribute_not_exists(sk)"
                        ),
                    }
                },
                {
                    # Creates record with event information
                    "Put": {
                        "TableName": environ["DYNAMODB_TABLE"],
                        "Item": staged_event,
                    }
                },
                {
                    # Creates record for event rsvp statistics
                    "Put": {
                        "TableName": environ["DYNAMODB_TABLE"],
                        "Item": get_statistics_record_for(staged_event),
                    }
                },
                {
                    # Updates organizer statistics with new event
                    "Update": get_organizer_event_update_query(event["mailto"])
                },
            ]
        )

        send_notification_of_new_event(event, organizer)
    except ClientError as error:
        if error.response["Error"]["Code"] == "TransactionCanceledException":
            logging.warning("Event record already exists")
            send_notification_of_updated_event(event)
        else:
            raise

        response = error.response

    return response


def get_organizer_event_update_query(organizer_email):
    """Get query to increase sent events in organizer sending statistics."""
    return {
        "TableName": environ["DYNAMODB_TABLE"],
        "Key": {
            "pk": {"S": f"organizer#{organizer_email}"},
            "sk": {"S": f"organizer_statistics#{organizer_email}"},
        },
        "UpdateExpression": (
            "SET #events = " "if_not_exists(#events, :zero) + :inc"
        ),
        "ExpressionAttributeNames": {"#events": "events"},
        "ExpressionAttributeValues": {":inc": {"N": "1"}, ":zero": {"N": "0"}},
    }


def get_original_event_record_from(request):
    """Record template for original_uid to uid lookup."""
    return {
        "pk": {"S": "original_event#{}".format(request["original_uid"]["S"])},
        "sk": {"S": "original_event#{}".format(request["original_uid"]["S"])},
        "uid": {"S": request["uid"]["S"]},
        "mailto": {"S": request["mailto"]["S"]},
    }


def get_statistics_record_for(request):
    """Record template for event statistics."""
    return {
        "pk": {"S": "event#{}".format(request["uid"]["S"])},
        "sk": {"S": "event_statistics#{}".format(request["uid"]["S"])},
        "tenant": {"S": "thirtyone"},
        "mailto": {"S": request["mailto"]["S"]},
        "attendees": {"N": "0"},
        "origin": {"M": {}},
        "prodid": {"M": {}},
        "rsvp": {
            "M": {
                "accepted": {"N": "0"},
                "declined": {"N": "0"},
                "noaction": {"N": "0"},
                "tentative": {"N": "0"},
            }
        },
    }


def send_notification_of_new_event(event, organizer):
    """Send notification of successful event create."""
    event.pop("description", None)

    event.update(
        {
            "events": organizer.get("statistics", {}).get("events", 1),
            "paid": organizer["paid"],
        }
    )

    return aws.publish_sns_message(
        message=json.dumps({"default": json.dumps(event)}),
        arn=environ["NEW_EVENT_CREATED"],
        sns=sns,
    )


def event_is_original(uid):
    """Validate event is original uid."""
    return aws.get_dynamodb_record_for(
        "original_event#{}".format(uid),
        secondary_key="original_event#{}".format(uid),
        dynamodb=dynamodb,
        dynamodb_table=dynamodb_table,
    )


def send_notification_of_updated_event(event):
    """Send notification of updated event."""
    return aws.publish_sns_message(
        message=json.dumps({"default": json.dumps(event)}),
        arn=environ["EVENT_UPDATED"],
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


def clean_up_successful_task(identifier):
    """Cleanup successful task identifier."""
    return aws.delete_sqs_message(identifier, url=environ["SQS_URL"], sqs=sqs)


def cleanup_all(tasks):
    """Clean up all tasks."""
    if tasks.get("failed", False):
        # Forces SQS retry of failed events
        raise Exception("{} tasks failed".format(tasks["failed"]))

    return "Complete"


def configure_logging(level="WARNING"):
    """Configure logging."""
    root = logging.getLogger()

    if root.handlers:
        for handler in root.handlers:
            root.removeHandler(handler)

    logging.basicConfig(format="%(message)s", level=level)


# Variable Re-use
dynamodb = boto3.client("dynamodb", region_name=environ["REGION"])
dynamodb_table = environ["DYNAMODB_TABLE"]
sns = boto3.client("sns")
sqs = boto3.client("sqs")
configure_logging(environ.get("LOG_LEVEL", "WARNING"))
