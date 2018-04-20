#!/usr/bin/env python
'''Train a simple deep CNN on the CIFAR10 small images dataset.

Using TFRecord queues with Keras and multi-GPU enabled. Compare with:
https://github.com/avolkov1/keras_experiments/blob/master/examples/cifar/cifar10_cnn_mgpu.py
https://github.com/fchollet/keras/blob/master/examples/cifar10_cnn.py
https://github.com/tensorflow/models/blob/master/tutorials/image/cifar10/cifar10_multi_gpu_train.py

Use Tensorflow version 1.2.x and above for good performance.

Source: https://github.com/avolkov1/keras_experiments/blob/master/examples/cifar/cifar10_cnn_mgpu_tfqueue.py
- modifications: large batch_size
- using keras_tf_multigpu package
- measuring samples/sec
'''

from __future__ import print_function

import os
import sys
import time
from argparse import SUPPRESS

import keras.layers as KL
import numpy as np
import tensorflow as tf
from keras import backend as KB
from keras.callbacks import ModelCheckpoint
from keras.datasets import cifar10
from keras.models import Sequential, Model
from keras.optimizers import RMSprop
# from keras.utils.data_utils import get_file
from keras.utils import to_categorical
from parser_common import parser_def_mgpu, remove_options

from keras_tf_multigpu.avolkov1 import (get_available_gpus, print_mgpu_modelsummary)
from keras_tf_multigpu.avolkov1 import make_parallel
from keras_tf_multigpu.callbacks import SamplesPerSec

_DEVPROF = False

checkptfile = 'cifar10_cnn_tfrecord.weights.hdf5'


def parser_(desc):
    parser = parser_def_mgpu(desc)

    remove_options(parser, ['--nccl', '--enqueue', '--syncopt', '--rdma'])

    parser.add_argument(
        '--checkpt', action='store', nargs='?',
        const=checkptfile, default=SUPPRESS,
        help='S|Save (overwrites) and load the model weights if available.'
        '\nOptionally specify a file/filepath if the default name is '
        'undesired.\n(default: {})'.format(checkptfile))

    parser.add_argument('--aug', action='store_true', default=False,
                        help='S|Perform data augmentation on cifar10 set.\n')

    parser.add_argument('--logdevp', action='store_true', default=False,
                        help='S|Log device placement in Tensorflow.\n')

    parser.add_argument('--datadir', default=SUPPRESS,
                        help='Data directory with Cifar10 dataset.')

    args = parser.parse_args()

    return args


def make_model(train_input, num_classes, weights_file=None):
    '''
    :param train_input: Either tensorflow Tensor or tuple/list shape. Bad style
        since the parameter can be of different types, but seems Ok here.
    :type train_input: tf.Tensor or tuple/list
    '''
    model = Sequential()
    # model.add(KL.InputLayer(input_shape=inshape[1:]))
    if isinstance(train_input, tf.Tensor):
        model.add(KL.InputLayer(input_tensor=train_input))
    else:
        model.add(KL.InputLayer(input_shape=train_input))
    model.add(KL.Conv2D(32, (3, 3), padding='same'))
    model.add(KL.Activation('relu'))
    model.add(KL.Conv2D(32, (3, 3)))
    model.add(KL.Activation('relu'))
    model.add(KL.MaxPooling2D(pool_size=(2, 2)))
    model.add(KL.Dropout(0.25))

    model.add(KL.Conv2D(64, (3, 3), padding='same'))
    model.add(KL.Activation('relu'))
    model.add(KL.Conv2D(64, (3, 3)))
    model.add(KL.Activation('relu'))
    model.add(KL.MaxPooling2D(pool_size=(2, 2)))
    model.add(KL.Dropout(0.25))

    model.add(KL.Flatten())
    model.add(KL.Dense(512))
    model.add(KL.Activation('relu'))
    model.add(KL.Dropout(0.5))
    model.add(KL.Dense(num_classes))
    model.add(KL.Activation('softmax'))

    if weights_file is not None and os.path.exists(weights_file):
        model.load_weights(weights_file)

    return model


def cifar10_load_data(path):
    """Loads CIFAR10 dataset.

    # Returns
        Tuple of Numpy arrays: `(x_train, y_train), (x_test, y_test)`.
    """
    dirname = 'cifar-10-batches-py'
    # origin = 'http://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz'
    # path = get_file(dirname, origin=origin, untar=True)
    path_ = os.path.join(path, dirname)

    num_train_samples = 50000

    x_train = np.zeros((num_train_samples, 3, 32, 32), dtype='uint8')
    y_train = np.zeros((num_train_samples,), dtype='uint8')

    for i in range(1, 6):
        fpath = os.path.join(path_, 'data_batch_' + str(i))
        data, labels = cifar10.load_batch(fpath)
        x_train[(i - 1) * 10000: i * 10000, :, :, :] = data
        y_train[(i - 1) * 10000: i * 10000] = labels

    fpath = os.path.join(path_, 'test_batch')
    x_test, y_test = cifar10.load_batch(fpath)

    y_train = np.reshape(y_train, (len(y_train), 1))
    y_test = np.reshape(y_test, (len(y_test), 1))

    if KB.image_data_format() == 'channels_last':
        x_train = x_train.transpose(0, 2, 3, 1)
        x_test = x_test.transpose(0, 2, 3, 1)

    return (x_train, y_train), (x_test, y_test)


def main(argv=None):
    '''
    '''
    main.__doc__ = __doc__
    argv = sys.argv if argv is None else sys.argv.extend(argv)
    desc = main.__doc__  # .format(os.path.basename(__file__))
    # CLI parser
    args = parser_(desc)
    mgpu = 0 if getattr(args, 'mgpu', None) is None else args.mgpu

    checkpt = getattr(args, 'checkpt', None)
    checkpt_flag = False if checkpt is None else True
    filepath = checkpt
    # print('CHECKPT:', checkpt)

    gdev_list = get_available_gpus(mgpu or 1)
    ngpus = len(gdev_list)

    batch_size_1gpu = 512
    batch_size = batch_size_1gpu * ngpus
    num_classes = 10
    epochs = args.epochs
    data_augmentation = args.aug

    logdevp = args.logdevp

    datadir = getattr(args, 'datadir', None)

    # The data, shuffled and split between train and test sets:
    (x_train, y_train), (x_test, y_test) = cifar10_load_data(datadir) \
        if datadir is not None else cifar10.load_data()
    train_samples = x_train.shape[0]
    test_samples = y_test.shape[0]
    steps_per_epoch = train_samples // batch_size
    print('train_samples:', train_samples)
    print('batch_size:', batch_size)
    print('steps_per_epoch:', steps_per_epoch)
    # validations_steps = test_samples // batch_size
    print(x_train.shape[0], 'train samples')
    print(x_test.shape[0], 'test samples')

    x_train = x_train.astype('float32')
    x_test = x_test.astype('float32')
    x_train /= 255
    x_test /= 255

    y_train = to_categorical(y_train, num_classes).astype(np.float32)
    y_test = to_categorical(y_test, num_classes).astype(np.float32)

    # The capacity variable controls the maximum queue size
    # allowed when prefetching data for training.
    capacity = 10000

    # min_after_dequeue is the minimum number elements in the queue
    # after a dequeue, which ensures sufficient mixing of elements.
    # min_after_dequeue = 3000

    # If `enqueue_many` is `False`, `tensors` is assumed to represent a
    # single example.  An input tensor with shape `[x, y, z]` will be output
    # as a tensor with shape `[batch_size, x, y, z]`.
    #
    # If `enqueue_many` is `True`, `tensors` is assumed to represent a
    # batch of examples, where the first dimension is indexed by example,
    # and all members of `tensors` should have the same size in the
    # first dimension.  If an input tensor has shape `[*, x, y, z]`, the
    # output will have shape `[batch_size, x, y, z]`.
    # enqueue_many = True

    # Force input pipeline to CPU:0 to avoid data operations ending up on GPU
    # and resulting in a slow down for multigpu case due to comm overhead.
    with tf.device('/cpu:0'):
        # if no augmentation can go directly from numpy arrays
        # x_train_batch, y_train_batch = tf.train.shuffle_batch(
        #     tensors=[x_train, y_train],
        #     # tensors=[x_train, y_train.astype(np.int32)],
        #     batch_size=batch_size,
        #     capacity=capacity,
        #     min_after_dequeue=min_after_dequeue,
        #     enqueue_many=enqueue_many,
        #     num_threads=8)

        input_images = tf.constant(x_train.reshape(train_samples, -1))
        print('train_samples', train_samples)
        print('input_images', input_images.shape)
        image, label = tf.train.slice_input_producer(
            [input_images, y_train], shuffle=True)
        # If using num_epochs=epochs have to:
        #     sess.run(tf.local_variables_initializer())
        #     and maybe also: sess.run(tf.global_variables_initializer())
        image = tf.reshape(image, x_train.shape[1:])
        print('image', image.shape)

        test_images = tf.constant(x_test.reshape(test_samples, -1))
        test_image, test_label = tf.train.slice_input_producer(
            [test_images, y_test], shuffle=False)
        test_image = tf.reshape(test_image, x_train.shape[1:])

        if data_augmentation:
            print('Using real-time data augmentation.')
            # Randomly flip the image horizontally.
            distorted_image = tf.image.random_flip_left_right(image)

            # Because these operations are not commutative, consider
            # randomizing the order their operation.
            # NOTE: since per_image_standardization zeros the mean and
            # makes the stddev unit, this likely has no effect see
            # tensorflow#1458.
            distorted_image = tf.image.random_brightness(
                distorted_image, max_delta=63)
            distorted_image = tf.image.random_contrast(
                distorted_image, lower=0.2, upper=1.8)

            # Subtract off the mean and divide by the variance of the
            # pixels.
            image = tf.image.per_image_standardization(distorted_image)

            # Do this for testing as well if standardizing
            test_image = tf.image.per_image_standardization(test_image)

        # Use tf.train.batch if slice_input_producer shuffle=True,
        # otherwise use tf.train.shuffle_batch. Not sure which way is faster.
        x_train_batch, y_train_batch = tf.train.batch(
            [image, label],
            batch_size=batch_size,
            capacity=capacity,
            num_threads=8)

        print('x_train_batch:', x_train_batch.shape)

        # x_train_batch, y_train_batch = tf.train.shuffle_batch(
        #     tensors=[image, label],
        #     batch_size=batch_size,
        #     capacity=capacity,
        #     min_after_dequeue=min_after_dequeue,
        #     num_threads=8)

        x_test_batch, y_test_batch = tf.train.batch(
            [test_image, test_label],
            batch_size=batch_size,
            capacity=capacity,
            num_threads=8,
            name='test_batch',
            shared_name='test_batch')

    x_train_input = KL.Input(tensor=x_train_batch)

    print('x_train_input', x_train_input)

    gauge = SamplesPerSec(batch_size)
    callbacks = [gauge]

    if _DEVPROF or logdevp:  # or True:
        # Setup Keras session using Tensorflow
        config = tf.ConfigProto(allow_soft_placement=True,
                                log_device_placement=True)
        # config.gpu_options.allow_growth = True
        tfsess = tf.Session(config=config)
        KB.set_session(tfsess)

    model_init = make_model(x_train_input, num_classes,
                            filepath if checkpt_flag else None)
    x_train_out = model_init.output
    # model_init.summary()

    lr = 0.0001 * ngpus
    if ngpus > 1:
        model = make_parallel(model_init, gdev_list)
    else:
        # Must re-instantiate model per API below otherwise doesn't work.
        model_init = Model(inputs=[x_train_input], outputs=[x_train_out])
        model = model_init

    opt = RMSprop(lr=lr, decay=1e-6)
    # Let's train the model using RMSprop
    model.compile(loss='categorical_crossentropy',
                  optimizer=opt,
                  metrics=['accuracy'],
                  target_tensors=[y_train_batch])

    print_mgpu_modelsummary(model)  # will print non-mgpu model as well

    if checkpt_flag:
        checkpoint = ModelCheckpoint(filepath, monitor='acc', verbose=1,
                                     save_best_only=True)
        callbacks = [checkpoint]

    # Start the queue runners.
    sess = KB.get_session()
    # sess.run([tf.local_variables_initializer(),
    #           tf.global_variables_initializer()])
    tf.train.start_queue_runners(sess=sess)

    # Fit the model using data from the TFRecord data tensors.
    coord = tf.train.Coordinator()
    threads = tf.train.start_queue_runners(sess, coord)

    val_in_train = False  # not sure how the validation part works during fit.

    start_time = time.time()
    model.fit(
        # validation_data=(x_test_batch, y_test_batch)
        # if val_in_train else None,  # validation data is not used???
        # validation_steps=validations_steps if val_in_train else None,
        validation_steps=val_in_train,
        steps_per_epoch=steps_per_epoch,
        epochs=epochs,
        callbacks=callbacks)
    elapsed_time = time.time() - start_time
    print('[{}] finished in {} ms'.format('TRAINING',
                                          int(elapsed_time * 1000)))
    gauge.print_results()

    weights_file = checkptfile  # './saved_cifar10_wt.h5'
    if not checkpt_flag:  # empty list
        model.save_weights(checkptfile)

    # Clean up the TF session.
    coord.request_stop()
    coord.join(threads)

    KB.clear_session()

    # Second Session. Demonstrate that the model works
    # test_model = make_model(x_test.shape[1:], num_classes,
    #                         weights_file=weights_file)
    test_model = make_model(x_test.shape[1:], num_classes)
    test_model.load_weights(weights_file)
    test_model.compile(loss='categorical_crossentropy',
                       optimizer=opt,
                       metrics=['accuracy'])

    if data_augmentation:
        x_proccessed = sess.run(x_test_batch)
        y_proccessed = sess.run(y_test_batch)
        loss, acc = test_model.evaluate(x_proccessed, y_proccessed)
    else:
        loss, acc = test_model.evaluate(x_test, y_test)

    print('\nTest loss: {0}'.format(loss))
    print('\nTest accuracy: {0}'.format(acc))


if __name__ == '__main__':
    main()
