import base64
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from os import environ

import boto3


# CodeCommit
def get_codecommit_file_for(
    path, repository=None, codecommit=boto3.client("codecommit")
):
    file = codecommit.get_file(repositoryName=repository, filePath=path)

    return file["fileContent"].decode("utf-8-sig")


# DynamoDB
def get_dynamodb_record_for(
    primary_key, secondary_key=None, dynamodb=None, dynamodb_table=None
):
    if secondary_key is None:
        secondary_key = primary_key

    dynamodb_record = dynamodb.get_item(
        TableName=dynamodb_table,
        Key={"pk": {"S": primary_key}, "sk": {"S": secondary_key}},
        ReturnConsumedCapacity="NONE",
    ).get("Item", {})

    formatted_record = {}

    if dynamodb_record:
        for field in dynamodb_record.keys():
            formatted_record.update(
                get_value_from_dynamodb(field, dynamodb_record[field])
            )

    return formatted_record


def get_value_from_dynamodb(field, value):
    if value.get("N", False):
        value["N"] = int(value["N"])

    return {field: value.popitem()[1]}


# S3
def get_s3_file_content_from(bucket=None, key=None, s3=boto3.client("s3")):
    return (
        s3.get_object(Bucket=bucket, Key=key)["Body"]
        .read()
        .decode("utf-8-sig")
    )


def get_s3_file_location_from(sns_notification):
    return (
        sns_notification["s3"]["bucket"]["name"],
        sns_notification["s3"]["object"]["key"],
    )


# SES
def send_ses_invite_to_attendee(
    ical=None,
    method="REQUEST",
    name="invite",
    subject=None,
    sender=None,
    recipient=None,
    charset="UTF-8",
    html=None,
    text=None,
    ses=boto3.client("ses", region_name="us-west-2"),
):
    """Sends raw email with ical to attendees."""
    email = MIMEMultipart("mixed")
    email["Subject"] = subject
    email["From"] = sender
    email["To"] = recipient
    email_body = MIMEText(html, "html")
    email.attach(email_body)

    email["Content-class"] = "urn:content-classes:calendarmessage"

    invite_text = MIMEText(str(ical), "plain")
    del invite_text["MIME-Version"]
    del invite_text["Content-Type"]

    invite_text.add_header(
        "Content-Type", f'text/calendar; charset="{charset}"; method={method}'
    )

    email.attach(invite_text)

    invite = MIMEText(base64.b64encode(str(ical).encode()).decode(), "plain")
    del invite["MIME-Version"]
    del invite["Content-Type"]
    del invite["Content-Disposition"]
    del invite["Content-Transfer-Encoding"]
    if method == "REQUEST":
        invite.add_header(
            "Content-Type", ' text/calendar; charset="UTF-8"; method=REQUEST'
        )
        invite.add_header("Content-Transfer-Encoding", "base64")

    elif method == "CANCEL":
        invite.add_header(
            "Content-Type",
            'text/calendar; charset="US-ASCII"; name={}.ics'.format(
                name.lower()
            ),
        )
        invite.add_header("Content-Transfer-Encoding", "base64")
        invite.add_header(
            "Content-Disposition",
            "attachment; filename={}.ics".format(name.lower()),
        )

    email.attach(invite)

    response = ses.send_raw_email(
        Source=sender,
        Destinations=[recipient],
        RawMessage={"Data": email.as_string()},
    )

    return response


def send_ses_standard_email(
    ses=None,
    subject=None,
    sender=None,
    recipient=[],
    charset="UTF-8",
    html=None,
    text=None,
):
    """Sends standard email."""
    destination = {"ToAddresses": recipient.split(",")}
    message = {"Subject": {"Charset": charset, "Data": subject}}

    if html:
        message["Body"] = {"Html": {"Charset": charset, "Data": html}}

    if text:
        message["Body"] = {"Text": {"Charset": charset, "Data": text}}

    response = ses.send_email(
        Destination=destination, Message=message, Source=sender
    )

    return response


# SNS
def publish_sns_message(message="", arn=None, sns=boto3.client("sns")):
    return sns.publish(TargetArn=arn, MessageStructure="json", Message=message)


# SQS
def delete_sqs_message(id, url=None, sqs=boto3.client("sqs")):
    return sqs.delete_message(QueueUrl=url, ReceiptHandle=id)


def get_sqs_message_with_s3_sns_notification_from(lambda_event):
    return json.loads(json.loads(lambda_event["body"])["Message"])[
        "Records"
    ].pop()


def get_sqs_message_with_sns_notification_from(lambda_event):
    return json.loads(json.loads(lambda_event["body"])["Message"])
