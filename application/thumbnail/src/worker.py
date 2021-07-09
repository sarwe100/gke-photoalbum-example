#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import logging
import os
import sys
import tempfile

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from PIL import Image

from google.cloud import pubsub_v1, storage, vision


project_id = os.environ['PROJECT_ID']
dbuser = os.environ['DB_USER']
dbpass = os.environ['DB_PASS']

subscription_name = 'thumbnail-workers'
bucket_name = '{}-photostore'.format(project_id)
content_types = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                 'png': 'image/png', 'gif': 'image/gif'}

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = \
    'mysql+pymysql://{}:{}@localhost:3306/photo_db'.format(dbuser, dbpass)
db = SQLAlchemy(app)

subscriber = pubsub_v1.SubscriberClient()
subscription_path = subscriber.subscription_path(
    project_id, subscription_name)


class Photo(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  filename = db.Column(db.String(128))
  label = db.Column(db.String(64))
  has_thumbnail = db.Column(db.Boolean)

  def __init__(self, filename):
    self.filename = filename
    self.label = None
    self.has_thumbnail = False


def create_thumbnail(filename):
  bucket = storage.Client().get_bucket(bucket_name)

  with tempfile.NamedTemporaryFile() as temp:
    blob = bucket.blob(filename)
    blob.download_to_filename(temp.name)
    im = Image.open(temp.name)
    im.thumbnail((128, 128), Image.ANTIALIAS)

    extention = filename.split('.')[-1].lower()
    temp_filename = '{}.{}'.format(temp.name, extention)
    im.save(temp_filename)
    content_type = content_types[extention]
    blob = bucket.blob('thumbnails/{}'.format(filename))
    blob.upload_from_filename(temp_filename, content_type=content_type)
    blob.make_public()


def update_db(filename):
  vision_client = vision.ImageAnnotatorClient()
  image = vision.Image()
  image.source.image_uri = 'gs://{}/{}'.format(bucket_name, filename)
  response = vision_client.label_detection(image=image, max_results=3)

  photo = db.session.query(Photo).filter_by(filename=filename).first()
  labels = [label.description for label in response.label_annotations]
  photo.label = ', '.join(labels)
  photo.has_thumbnail = True
  db.session.commit()


def callback(message):
  try:
    filename = message.data.decode()
    message.ack()
    create_thumbnail(filename)
    update_db(filename)
  except Exception as e:
    print('Something wrong happened: {}'.format(e.args))


streaming_pull_future = subscriber.subscribe(
  subscription_path, callback=callback)

with subscriber:
  try:
    streaming_pull_future.result()
  except TimeoutError:
    streaming_pull_future.cancel()
    streaming_pull_future.result()

