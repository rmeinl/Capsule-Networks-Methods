# -*- coding: utf-8 -*-
"""CapsClass.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1eDggHah7vZG2K-ZDFFBTDZ8fvOuR5AXu
"""

import matplotlib.pyplot as plt
import numpy as np
import tensorflow as tf
import math

def random_mini_batches(X, y, mini_batch_size):
    m = X.shape[0]
    mini_batches = []
    
    #Step 1: Shuffle
    permutation = list(np.random.permutation(m))
    shuffled_X = X[permutation, :]
    shuffled_Y = y[permutation]
    
    #Step 2: Partition
    num_complete_minibatches = math.floor(m/mini_batch_size)
    for k in range(0, num_complete_minibatches):
        mini_batch_X = shuffled_X[k * mini_batch_size : (k+1) * mini_batch_size, :]
        mini_batch_Y = shuffled_Y[k * mini_batch_size : (k+1) * mini_batch_size]
        mini_batch = (mini_batch_X, mini_batch_Y)
        mini_batches.append(mini_batch)

    #Handling the end case
    if m % mini_batch_size != 0:
        mini_batch_X = shuffled_X[: m - mini_batch_size * num_complete_minibatches, :]
        mini_batch_Y = shuffled_Y[: m - mini_batch_size * num_complete_minibatches]
        mini_batch = (mini_batch_X, mini_batch_Y)
        mini_batches.append(mini_batch)
        
    return mini_batches

def safe_norm(s, axis=-1, epsilon=1e-7, keepdims=False, name=None):
  with tf.name_scope(name, default_name="safe_norm"):
    squared_norm = tf.reduce_sum(tf.square(s), axis=axis, keepdims=keepdims)
    return tf.sqrt(squared_norm + epsilon)

def squash(s, axis=-1, epsilon=1e-7, name=None):
  with tf.name_scope(name, default_name="squash"):
    squared_norm = tf.reduce_sum(tf.square(s), axis=axis, keepdims=True)
    safe_norm = tf.sqrt(squared_norm + epsilon)
    squash_factor = squared_norm / (1. + squared_norm)
    unit_vector = s / safe_norm
    return squash_factor * unit_vector

def primary_caps(conv1, caps1_n_maps, caps1_n_dims, caps1_n_caps, kernel_size=9, strides=2):
  conv2 = tf.layers.conv2d(conv1, filters=caps1_n_maps*caps1_n_dims, kernel_size=kernel_size, strides=strides, activation=tf.nn.relu)
  caps1_raw = tf.reshape(conv2, [-1, caps1_n_caps, caps1_n_dims])
  
  return squash(caps1_raw)

def digit_caps(caps1_output, W, caps2_n_caps, batch_size):
  W_tiled = tf.tile(W, [batch_size, 1, 1, 1, 1])
  
  caps1_output_expanded = tf.expand_dims(caps1_output, -1)
  caps1_output_tile = tf.expand_dims(caps1_output_expanded, 2)
  caps1_output_tiled = tf.tile(caps1_output_tile, [1, 1, caps2_n_caps, 1, 1])
  
  caps2_predicted = tf.matmul(W_tiled, caps1_output_tiled)
  
  return caps2_predicted

def routing(caps2_predicted, raw_weights, caps1_n_caps):
  def condition(input, counter, caps2_predicted):
      return tf.less(counter, 2)

  def loop_body(raw_weights, counter, caps2_predicted):
      # apply softmax function to compute the routing weights
      routing_weights = tf.nn.softmax(raw_weights, axis=2)

      # compute the weighted sum of all the predicted output vectors
      weighted_predictions = tf.multiply(routing_weights, caps2_predicted)
      weighted_sum = tf.reduce_sum(weighted_predictions, axis=1, keepdims=True)

      # apply the squash function to get outputs
      caps2_output = squash(weighted_sum, axis=-2)

      # tile the array and compute the scalar product
      caps2_output_tiled = tf.tile(caps2_output, [1, caps1_n_caps, 1, 1, 1])
      agreement = tf.matmul(caps2_predicted, caps2_output_tiled, transpose_a=True)

      # update the routing weights by adding the scalar product
      raw_weights = tf.add(raw_weights, agreement)

      return raw_weights, tf.add(counter, 1), caps2_output_tiled
  
  with tf.name_scope("routing"):
    counter = tf.constant(1)
    
    raw_weights, counter, result = tf.while_loop(condition, loop_body, [raw_weights, counter, caps2_predicted])
    caps2_output = tf.expand_dims(result[:, 1, :, :, :], 1)
    
    return caps2_output

def caps_predicted_output(caps2_output):
  # the length of the output vectors represent the class probabilites, use norm
  y_proba = safe_norm(caps2_output, axis=-2)
  
  #select the one with the highest estimated probability to predict class
  y_proba_argmax = tf.argmax(y_proba, axis=2)
  y_pred = tf.squeeze(y_proba_argmax, axis=[1,2])
  
  return y_pred

def reconstruction_input(caps2_output, mask_with_labels, y, y_pred, caps2_n_caps, caps2_n_dims):
  # define the reconstruction targets
  reconstruction_targets = tf.cond(mask_with_labels, lambda:y, lambda:y_pred)
  
  # create the reconstruction mask 
  reconstruction_mask = tf.one_hot(reconstruction_targets, depth=caps2_n_caps)
  reconstruction_mask_reshaped = tf.reshape(reconstruction_mask, [-1, 1, caps2_n_caps, 1, 1])
  
  #apply the mask
  caps2_output_masked = tf.multiply(caps2_output, reconstruction_mask_reshaped)
  decoder_input = tf.reshape(caps2_output_masked, [-1, caps2_n_caps * caps2_n_dims])
  
  return decoder_input

def compute_loss(caps2_output, decoder_output, T, X_flat, alpha=0.0005, m_plus=0.9, m_minus=0.1, lambda_=0.5):
  # compute the norm of the output vector for each output capsule and each instance
  caps2_output_norm = safe_norm(caps2_output, axis=-2, keepdims=True)
  
  # compute present error
  present_error_raw = tf.square(tf.maximum(0., m_plus - caps2_output_norm))
  present_error = tf.reshape(present_error_raw, shape=(-1,10))
  
  # compute absent error
  absent_error_raw = tf.square(tf.maximum(0., caps2_output_norm - m_minus))
  absent_error = tf.reshape(absent_error_raw, shape=(-1,10))
  
  # compute loss for each for each instance and each class
  L = tf.add(T * present_error, lambda_ * (1.0 - T) * absent_error)
  
  # sum the digit losses and compute the mean over all instances
  margin_loss = tf.reduce_mean(tf.reduce_sum(L, axis=1))
  
  # squared difference between the input image and the reconstructed image
  squared_difference = tf.square(X_flat - decoder_output)
  reconstruction_loss = tf.reduce_mean(squared_difference)
  
  # sum of the margin loss and the reconstruction loss
  # scaled down by alpha to ensure the margin loss dominates training
  loss = tf.add(margin_loss, alpha * reconstruction_loss)
  
  return loss

def create_placeholders(img_size, n_c):
  # input X and y
  X = tf.placeholder(shape=[None, img_size, img_size, n_c], dtype=tf.float32)
  y = tf.placeholder(shape=[None], dtype=tf.int64)
  
  # placeholder for mask
  mask_with_labels = tf.placeholder_with_default(False, shape=())
  
  return X, y, mask_with_labels

def initialize_parameters(parameters, init_sigma=0.1):
  # init weights for digit caps
  W_init = tf.random_normal(shape=(1, parameters['caps1_n_caps'], parameters['caps2_n_caps'], parameters['caps2_n_dims'], parameters['caps1_n_dims']),
                         stddev=init_sigma, dtype=tf.float32)
  W = tf.Variable(W_init)
  
  return W

def forward_propagation(X, y, mask_with_labels, parameters, img_size):
  # forward pass
  conv1 = tf.layers.conv2d(X, filters=256, kernel_size=9, strides=1, activation=tf.nn.relu)
  caps1_output = primary_caps(conv1, parameters['caps1_n_maps'], parameters['caps1_n_dims'], parameters['caps1_n_caps'], kernel_size=9, strides=2)
  caps2_predicted = digit_caps(caps1_output, parameters['W'], parameters['caps2_n_caps'], tf.shape(X)[0])
  #initialize new routing weights to zero
  raw_weights = tf.zeros([tf.shape(X)[0], parameters['caps1_n_caps'], parameters['caps2_n_caps'], 1, 1], dtype=np.float32)
  caps2_output = routing(caps2_predicted, raw_weights, parameters['caps1_n_caps'])
  
  # make prediction
  y_pred = caps_predicted_output(caps2_output)
  
  # build decoder
  decoder_input = reconstruction_input(caps2_output, mask_with_labels, y, y_pred, parameters['caps2_n_caps'], parameters['caps2_n_dims'])
  hidden1 = tf.layers.dense(decoder_input, parameters['n_hidden1'], activation=tf.nn.relu)
  hidden2 = tf.layers.dense(hidden1, parameters['n_hidden2'], activation=tf.nn.relu)
  decoder_output = tf.layers.dense(hidden2, img_size*img_size, activation=tf.nn.sigmoid)
  """fc1 = tf.contrib.layers.fully_connected(decoder_input, num_outputs=parameters['n_hidden1'])
  fc1 = tf.reshape(fc1, shape=(batch_size, 5, 5, 16))
  upsample1 = tf.image.resize_nearest_neighbor(fc1, (8, 8))
  conv1 = tf.layers.conv2d(upsample1, 4, (3,3), padding='same', activation=tf.nn.relu)

  upsample2 = tf.image.resize_nearest_neighbor(conv1, (16, 16))
  conv2 = tf.layers.conv2d(upsample2, 8, (3,3), padding='same', activation=tf.nn.relu)

  upsample3 = tf.image.resize_nearest_neighbor(conv2, (32, 32))
  conv6 = tf.layers.conv2d(upsample3, 16, (3,3), padding='same', activation=tf.nn.relu)

  upsample4 = tf.image.resize_nearest_neighbor(conv6, (66, 66))
  conv12 = tf.layers.conv2d(upsample4, 32, (3,3), padding='same', activation=tf.nn.relu)
  
  # 3 channel for RGG
  logits = tf.layers.conv2d(conv12, 3, (3,3), padding='same', activation=None)
  decoder_output = tf.nn.sigmoid(logits, name='decoded')
  #tf.summary.image('reconstruction_img', decoded)"""
  
  return caps2_output, decoder_output, y_pred

def model(X_train, y_train, X_val, y_val, parameters, n_epochs = 10, batch_size = 50, img_size=(28,28,1), restore_checkpoint=True):
 
    tf.reset_default_graph()                         
    m = X_train.shape[0]
    m_val = X_val.shape[0]
    best_loss_val = np.infty
    checkpoint_path = "./checkpoints"
    
    # Create Placeholders
    X, y, mask_with_labels  = create_placeholders(img_size[0], img_size[2])

    # Initialize parameters
    W = initialize_parameters(parameters)
    parameters['W'] = W
    
    # Forward propagation
    caps2_output, decoder_output, y_pred = forward_propagation(X, y, mask_with_labels, parameters, img_size[0])
    
    T = tf.one_hot(y, depth=caps2_n_caps)
    X_flat = tf.reshape(X, [-1, img_size[0] * img_size[0]])

    # Cost function
    loss = compute_loss(caps2_output, decoder_output, T, X_flat)
    
    # get accuracy
    correct = tf.equal(y, y_pred)
    accuracy = tf.reduce_mean(tf.cast(correct, tf.float32))
    
    # Backpropagation
    optimizer = tf.train.AdamOptimizer().minimize(loss)
    
    # Initialize all the variables
    init = tf.global_variables_initializer()
    saver = tf.train.Saver()

    # Start the session to compute the tensorflow graph
    with tf.Session() as sess:
        
        if restore_checkpoint and tf.train.checkpoint_exists(checkpoint_path):
          saver.restore(sess, checkpoint_path)
        else:
          init.run()
        
        # Do the training loop
        for epoch in range(n_epochs):

            num_batches = int(m / batch_size)
            minibatches = random_mini_batches(X_train, y_train, batch_size)

            for i, minibatch in enumerate(minibatches):

                # Select a minibatch
                (minibatch_X, minibatch_Y) = minibatch
                
                # Run the session
                _ , loss_train = sess.run([optimizer, loss], feed_dict={X: minibatch_X.reshape([-1, img_size[0], img_size[1], img_size[2]]),
                                                                        y: minibatch_Y, mask_with_labels: True})
                
                print("\rIteration: {}/{} ({:.1f}%)  Loss: {:.5f}".format(
                  i, num_batches, i * 100 / num_batches, loss_train),
                end="")

            #at the end of each epoch, measure validation loss and accuracy
            loss_vals = []
            acc_vals = []
            num_batches_val = int(m_val / batch_size)
            minibatches_val = random_mini_batches(X_val, y_val, batch_size)
            
            for i, minibatch_val in enumerate(minibatches_val):

                # Select a minibatch
                (minibatch_X, minibatch_Y) = minibatch_val
                
                # Run the session
                loss_val , acc_val = sess.run([loss, accuracy], feed_dict={X: minibatch_X.reshape([-1, img_size[0], img_size[1], img_size[2]]), 
                                                                           y: minibatch_Y})
                loss_vals.append(loss_val)
                acc_vals.append(acc_val)
                
                print("\rEvaluating the model: {}/{} ({:.1f}%)".format(
                      i, num_batches_val,
                      i * 100 / num_batches_val),
                   end=" " * 10)
                
            loss_val = np.mean(loss_vals)
            acc_val = np.mean(acc_vals)
            print("\rEpoch: {}  Val accuracy: {:.4f}%  Loss: {:.6f}{}".format(
                    epoch + 1, acc_val * 100, loss_val,
                    " (improved)" if loss_val < best_loss_val else ""))
            
            # Save model if improved
            if loss_val < best_loss_val:
              save_path = saver.save(sess, checkpoint_path)
              best_loss_val = loss_val


