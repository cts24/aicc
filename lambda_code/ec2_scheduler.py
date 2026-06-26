import boto3

ec2 = boto3.client('ec2')

def lambda_handler(event, context):
    action = event.get('action', 'stop')
    filters = [{'Name': 'tag:AutoStop', 'Values': ['night']}]
    if action == 'start':
        filters.append({'Name': 'instance-state-name', 'Values': ['stopped']})
    else:
        filters.append({'Name': 'instance-state-name', 'Values': ['running']})

    instances = ec2.describe_instances(Filters=filters)
    ids = []
    for r in instances['Reservations']:
        for i in r['Instances']:
            ids.append(i['InstanceId'])

    if not ids:
        return {'status': 'no_instances', 'action': action}

    if action == 'start':
        ec2.start_instances(InstanceIds=ids)
    else:
        ec2.stop_instances(InstanceIds=ids)

    return {'status': 'ok', 'action': action, 'instance_ids': ids}
