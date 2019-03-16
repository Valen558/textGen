# -*- coding: utf-8 -*-
"""textGenModel

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1wWwGVMdJTAkvJyLXIxXhdgWQUSLrtBbC

## Predict AIGramp text with Cloud TPUs and Keras

### Download data

Download textfiles from AIGramp. You use snippets from this file as the *training data* for the model. The *target* snippet is offset by one character.
"""

!wget --show-progress  -O /content/merged.txt http://aigramp.com/texts/merged.txt
!wget --show-progress  -O /content/more.txt http://aigramp.com/texts/more/merged.txt
!wget --show-progress  -O /content/horror.txt http://aigramp.com/texts/horror/merged.txt
!wget --show-progress  -O /content/agathaCristie.txt http://aigramp.com/texts/agathaCristie/merged.txt

"""imports"""

import sys
import numpy as np
import six
import tensorflow as tf
import time
import os

"""### Build the data generator"""

# This address identifies the TPU we'll use when configuring TensorFlow.
TPU_WORKER = 'grpc://' + os.environ['COLAB_TPU_ADDR']

THE_TEXT = '/content/more.txt'

tf.logging.set_verbosity(tf.logging.INFO)

def transform(txt, pad_to=None):
  # drop any non-ascii characters
  output = np.asarray([ord(c) for c in txt if ord(c) < 255], dtype=np.int32)
  if pad_to is not None:
    output = output[:pad_to]
    output = np.concatenate([
        np.zeros([pad_to - len(txt)], dtype=np.int32),
        output,
    ])
  return output

def training_generator(seq_len=100, batch_size=1024):
  """A generator yields (source, target) arrays for training."""
  with tf.gfile.GFile(THE_TEXT, 'r') as f:
    txt = f.read()

  tf.logging.info('Input text [%d] %s', len(txt), txt[:50])
  source = transform(txt)
  while True:
    offsets = np.random.randint(0, len(source) - seq_len, batch_size)

    # Our model uses sparse crossentropy loss, but Keras requires labels
    # to have the same rank as the input logits.  We add an empty final
    # dimension to account for this.
    yield (
        np.stack([source[idx:idx + seq_len] for idx in offsets]),
        np.expand_dims(
            np.stack([source[idx + 1:idx + seq_len + 1] for idx in offsets]),
            -1),
    )

six.next(training_generator(seq_len=10, batch_size=1))

"""### Build the model

The model is defined as a two-layer, forward-LSTM—with two changes from the `tf.keras` standard LSTM definition:

1. Define the input `shape` of the model to comply with the [XLA compiler](https://www.tensorflow.org/performance/xla/)'s static shape requirement.
2. Use `tf.train.Optimizer` instead of a standard Keras optimizer (Keras optimizer support is still experimental).
"""

EMBEDDING_DIM = 512

def lstm_model(seq_len=100, batch_size=None, stateful=True):
  """Language model: predict the next word given the current word."""
  source = tf.keras.Input(
      name='seed', shape=(seq_len,), batch_size=batch_size, dtype=tf.int32)

  embedding = tf.keras.layers.Embedding(input_dim=256, output_dim=EMBEDDING_DIM)(source)
  lstm_1 = tf.keras.layers.LSTM(EMBEDDING_DIM, stateful=stateful, return_sequences=True)(embedding)
  lstm_2 = tf.keras.layers.LSTM(EMBEDDING_DIM, stateful=stateful, return_sequences=True)(lstm_1)
  predicted_char = tf.keras.layers.TimeDistributed(tf.keras.layers.Dense(256, activation='softmax'))(lstm_2)
  model = tf.keras.Model(inputs=[source], outputs=[predicted_char])
  model.compile(
      optimizer=tf.train.RMSPropOptimizer(learning_rate=0.01),
      loss='sparse_categorical_crossentropy',
      metrics=['sparse_categorical_accuracy'])
  return model

"""### Train the model

The `tf.contrib.tpu.keras_to_tpu_model` function converts a `tf.keras` model to an equivalent TPU version. You then use the standard Keras methods to train: `fit`, `predict`, and `evaluate`.
"""

tf.keras.backend.clear_session()

training_model = lstm_model(seq_len=100, batch_size=128, stateful=False)

tpu_model = tf.contrib.tpu.keras_to_tpu_model(
    training_model,
    strategy=tf.contrib.tpu.TPUDistributionStrategy(
        tf.contrib.cluster_resolver.TPUClusterResolver(TPU_WORKER)))

tpu_model.fit_generator(
    training_generator(seq_len=100, batch_size=1024),
    steps_per_epoch=100,
    epochs=15,
)
tpu_model.save_weights('/tmp/bard.h5', overwrite=True)

"""### Make predictions with the model

Use the trained model to make predictions and generate your own Shakespeare-esque play.
Start the model off with a *seed* sentence, then generate 250 characters from it. The model makes five predictions from the initial seed.
"""

BATCH_SIZE = 50
PREDICT_LEN = 2000

# Keras requires the batch size be specified ahead of time for stateful models.
# We use a sequence length of 1, as we will be feeding in one character at a 
# time and predicting the next character.
prediction_model = lstm_model(seq_len=1, batch_size=BATCH_SIZE, stateful=True)
prediction_model.load_weights('/tmp/bard.h5')

# We seed the model with our initial string, copied BATCH_SIZE times

seed_txt = 'Tom bought an apartment and a car'
seed = transform(seed_txt)
seed = np.repeat(np.expand_dims(seed, 0), BATCH_SIZE, axis=0)

# First, run the seed forward to prime the state of the model.
prediction_model.reset_states()
for i in range(len(seed_txt) - 1):
  prediction_model.predict(seed[:, i:i + 1])

# Now we can accumulate predictions!
predictions = [seed[:, -1:]]
for i in range(PREDICT_LEN):
  last_word = predictions[-1]
  next_probits = prediction_model.predict(last_word)[:, 0, :]
  
  # sample from our output distribution
  next_idx = [
      np.random.choice(256, p=next_probits[i])
      for i in range(BATCH_SIZE)
  ]
  predictions.append(np.asarray(next_idx, dtype=np.int32))
  

for i in range(BATCH_SIZE):
  print('Generated text %d\n\n:' % i)
  p = [predictions[j][i] for j in range(PREDICT_LEN)]
  generated = ''.join([chr(c) for c in p])
  print(generated)
  print()
  assert len(generated) == PREDICT_LEN, 'Generated text too short'

