'''
AlexNet-like model on CIFAR10.

Reference model: tensorflow/models/tutorials/image/cifar10/cifar10.py
Structure according to: https://github.com/tensorflow/benchmarks/blob/master/scripts/tf_cnn_benchmarks/alexnet_model.py
'''

from __future__ import print_function

import argparse

import keras
from keras.datasets import cifar10
from keras.layers import Conv2D, MaxPooling2D
from keras.layers import Input, Dense, Flatten
from keras.models import Model
from keras.preprocessing.image import ImageDataGenerator

num_classes = 10

def load_data():
    # The data, shuffled and split between train and test sets:
    (x_train, y_train), (x_test, y_test) = cifar10.load_data()
    print('x_train shape:', x_train.shape)
    print(x_train.shape[0], 'train samples')
    print(x_test.shape[0], 'test samples')

    x_train = x_train.astype('float32') / 255
    x_test = x_test.astype('float32') / 255

    # Convert class vectors to binary class matrices.
    y_train = keras.utils.to_categorical(y_train, num_classes)
    y_test = keras.utils.to_categorical(y_test, num_classes)

    return (x_train, x_test), (y_train, y_test)

def create_model(input_shape):
    input = Input(shape=input_shape)
    x = Conv2D(64, (5, 5), padding='same', activation='relu')(input)
    x = MaxPooling2D(pool_size=(3, 3), strides=(2, 2), padding='same')(x)
    # TODO: local response normalization
    x = Conv2D(64, (5, 5), padding='same', activation='relu')(x)
    # TODO: local response normalization
    x = MaxPooling2D(pool_size=(3, 3), strides=(2, 2), padding='same')(x)
    x = Flatten()(x)
    x = Dense(384, activation='relu')(x)
    x = Dense(192, activation='relu')(x)
    x = Dense(num_classes, activation='softmax')(x)
    output = x

    model = Model(inputs=input, outputs=output)

    # initiate RMSprop optimizer
    opt = keras.optimizers.rmsprop(lr=0.0001, decay=1e-6)

    # Let's train the model using RMSprop
    model.compile(loss='categorical_crossentropy',
                  optimizer=opt,
                  metrics=['accuracy'])

    model.summary()

    return model

def train(model, data, batch_size, epochs):
    (x_train, x_test), (y_train, y_test) = data
    if not data_augmentation:
        print('Not using data augmentation.')
        model.fit(x_train, y_train,
                  batch_size=batch_size,
                  epochs=epochs,
                  validation_data=(x_test, y_test),
                  shuffle=True)
    else:
        print('Using real-time data augmentation.')
        # This will do preprocessing and realtime data augmentation:
        datagen = ImageDataGenerator(
            featurewise_center=False,  # set input mean to 0 over the dataset
            samplewise_center=False,  # set each sample mean to 0
            featurewise_std_normalization=False,  # divide inputs by std of the dataset
            samplewise_std_normalization=False,  # divide each input by its std
            zca_whitening=False,  # apply ZCA whitening
            rotation_range=0,  # randomly rotate images in the range (degrees, 0 to 180)
            width_shift_range=0.1,  # randomly shift images horizontally (fraction of total width)
            height_shift_range=0.1,  # randomly shift images vertically (fraction of total height)
            horizontal_flip=True,  # randomly flip images
            vertical_flip=False)  # randomly flip images

        # Compute quantities required for feature-wise normalization
        # (std, mean, and principal components if ZCA whitening is applied).
        datagen.fit(x_train)

        # Fit the model on the batches generated by datagen.flow().
        model.fit_generator(datagen.flow(x_train, y_train,
                                         batch_size=batch_size),
                            steps_per_epoch=x_train.shape[0] // batch_size,
                            epochs=epochs,
                            validation_data=(x_test, y_test))

def parse_args():
    parser = argparse.ArgumentParser(description='AlexNet on CIFAR10.')
    parser.add_argument('-a', '--augment-data', default=True, type=bool,
                        help='Enable data augmentation')
    parser.add_argument('-b', '--batch-size', default=128, type=int,
                        help='Per-replica batch size')
    parser.add_argument('-e', '--epochs', default=10, type=int,
                        help='Number of epochs')

    return parser.parse_args()

if __name__ == '__main__':
    args = parse_args()

    print('arguments:', args)

    batch_size = args.batch_size
    epochs = args.epochs
    data_augmentation = args.augment_data

    data = load_data()
    (x_train, x_test), (y_train, y_test) = data
    model = create_model(x_train.shape[1:])
    train(model, data, batch_size, epochs)
