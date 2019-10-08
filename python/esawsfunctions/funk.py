import random
import boto3
import json
import pandas as pd

class NoDataInQueueError(Exception):
    '''
    Custom exception signifying that there is no data in the queue
    (the response did not contain messages)
    '''
    pass


def read_from_s3(bucket_name, file_name):
    """
    Given the name of the bucket and the filename(key), this function will
    return a file. File is JSON format.
    :param bucket_name: Name of the S3 bucket - Type: String
    :param file_name: Name of the file - Type: String
    :return: input_file: The JSON file in S3 - Type: String
    """
    s3 = boto3.resource('s3', region_name="eu-west-2")
    object = s3.Object(bucket_name, file_name)
    input_file = object.get()['Body'].read().decode('UTF-8')

    return input_file

def read_dataframe_from_s3(bucket_name, file_name):
    """
    Given the name of the bucket and the filename(key), this function will
    return contents of a file. File is DataFrame format.
    :param bucket_name: Name of the S3 bucket - Type: String
    :param file_name: Name of the file - Type: String
    :return: input_file: The JSON file in S3 loaded into dataframe table - Type: DataFrame
    """
    input_file = read_from_s3(bucket_name, file_name)
    json_content = json.loads(input_file)
    return pd.DataFrame(json_content)


def save_to_s3(bucket_name, output_file_name, output_data):
    """
    This function uploads a specified set of data to the s3 bucket under the given name.
    :param bucket_name: Name of the bucket you wish to upload too - Type: String.
    :param output_file_name: Name you want the file to be called on s3 - Type: String.
    :param output_data: The data that you wish to upload to s3 - Type: JSON.
    :return: None
    """
    s3 = boto3.resource('s3', region_name="eu-west-2")
    s3.Object(bucket_name, output_file_name).put(Body=output_data)


def send_sqs_message(queue_url, message, output_message_id):
    """
    This method is responsible for sending data to the SQS queue.
    :param queue_url: The url of the SQS queue. - Type: String
    :param message: The message/data you wish to send to the SQS queue - Type: String
    :param output_message_id: The label of the record in the SQS queue - Type: String
    :return: None
    """
    # MessageDeduplicationId is set to a random hash to overcome de-duplication,
    # otherwise modules could not be re-run in the space of 5 Minutes.
    sqs = boto3.client('sqs', region_name="eu-west-2")
    return sqs.send_message(QueueUrl=queue_url,
                            MessageBody=message,
                            MessageGroupId=output_message_id,
                            MessageDeduplicationId=str(random.getrandbits(128))
                            )


def send_sns_message(checkpoint, sns_topic_arn, module_name):
    """
    This method is responsible for sending a notification to the specified arn,
    so that it can be used to relay information for the BPM to use and handle.
    :param checkpoint: The current checkpoint location - Type: String.
    :param module_name: The name of the module currently being run - Type: String.
    :param sns_topic_arn: The arn of the sns topic you are directing the message at -
                          Type: String.
    :return: None
    """
    sns = boto3.client('sns', region_name="eu-west-2")
    sns_message = {
        "success": True,
        "module": module_name,
        "checkpoint": checkpoint,
        "message": "Completed " + module_name
    }

    return sns.publish(TargetArn=sns_topic_arn, Message=json.dumps(sns_message))


def send_sns_message_with_anomalies(checkpoint, anomalies, sns_topic_arn, module_name):
    """
    This method is responsible for sending a notification to the specified arn,
    so that it can be used to relay information for the BPM to use and handle.
    :param checkpoint: The current checkpoint location - Type: String.
    :param anomalies: Json formatted summary of data anomalies - Type: String.
    :param module_name: The name of the module currently being run - Type: String.
    :param sns_topic_arn: The arn of the sns topic you are directing the message at -
                          Type: String.
    :return: None
    """
    sns = boto3.client('sns', region_name='eu-west-2')
    sns_message = {
        "success": True,
        "module": module_name,
        "checkpoint": checkpoint,
        "anomalies": anomalies,
        "message": "Completed " + module_name

    }

    sns.publish(
        TargetArn=sns_topic_arn,
        Message=json.dumps(sns_message)
    )


def get_sqs_message(queue_url):
    """
    This method retrieves the data from the specified SQS queue.
    :param queue_url: The url of the SQS queue.
    :return: Messages from queue - Type: json string
    """
    sqs = boto3.client('sqs', region_name="eu-west-2")
    return sqs.receive_message(QueueUrl=queue_url)


def save_data(bucket_name, file_name, data, queue_url, message_id):
    '''
    Save data function stores data in s3 and passes the bucket & filename onto sqs queue.
    SQS only supports message length of 256k, so this function is to be used instead of send_sqs_message
     when the data size approaches this figure. Used in conjunction with get_data
    :param bucket_name: The name of the s3 bucket to use to save data - Type: String
    :param file_name: The name to give the file being saved - Type: String
    :param data: The data to be saved - Type Json string
    :param queue_url: The url of the queue to use in sending the file details - Type: String
    :param message_id: The label of the message sent to sqs(Message_group_id, what module sent the message)
    - Type: String
    :return: Nothing
    '''
    save_to_s3(bucket_name, file_name, data)
    sqs_message = json.dumps({"bucket": bucket_name, "key": file_name})
    send_sqs_message(queue_url, sqs_message, message_id)


def get_data(queue_url, bucket_name, key, incoming_message_group):
    '''
    Get data function recieves a message from an sqs queue, extracts the bucket and filename,
    then uses them to get the file from s3.
    If no messages are in the queue, or if the message does not come from the preceding module,
    the bucket_name and key given as parameters are used instead.

    SQS only supports message length of 256k, so this function is to be used instead of
    get_sqs_message when the data size approaches this figure. Used in conjunction with save_data

    Data is returned as a json string. To use as dataframe you will need to json.loads and pd.dataframe() the response.
    :param queue_url: The url of the queue to retrieve message from - Type: String
    :param bucket_name: The default bucket name to use if no message from previous module - Type: String
    :param key: The default file name to use if no message from the previous module - Type: String
    :param incoming_message_group: The name of the message group from previous module - Type: String
    :return data: The data from s3 - Type: Json
    :return receipt_handle: The receipt_handle of the incoming message(used to delete old message) - Type: String
    '''
    response = get_sqs_message(queue_url)
    receipt_handle = None
    if "Messages" not in response or \
            ("Messages" in response and
             (response['Messages'][0]['Attributes']['MessageGroupId'] != incoming_message_group)):
        bucket = bucket_name
        key = key
    else:
        message = response["Messages"][0]
        receipt_handle = message['ReceiptHandle']
        message = json.loads(message['Body'])
        bucket = message['bucket']
        key = message['key']
    data = read_from_s3(bucket, key)
    return data, receipt_handle