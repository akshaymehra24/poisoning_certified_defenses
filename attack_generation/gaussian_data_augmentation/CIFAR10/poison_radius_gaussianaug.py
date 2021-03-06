import numpy as np
import tensorflow as tf
from bilevel_poison_radius import bilevel_poisoning
from cifar10_keras_model import resnet_v1
config = tf.ConfigProto()
config.gpu_options.allow_growth = True

X_train = np.load("data/X_train.npy")
Y_train = np.load("data/Y_train.npy")
X_val = np.load("data/X_val.npy")
Y_val = np.load("data/Y_val.npy")
X_test = np.load("data/X_test.npy")
Y_test = np.load("data/Y_test.npy")

lr_p = 1E-3
lr_u = 1E-1
lr_v = 1E-3

sig = 1E-3

nepochs = 51

target = 8

niter1 = 10
niter2 = niter1

height = 32
width = 32
nch = 3
nclass = 10
eps = 0.03

total_batch_size = 200
val_batch_size = 20
batch_size_clean = int((nclass - 1) * total_batch_size / nclass)
batch_size_poisoned = int(total_batch_size / nclass)

k_macer = 5
sigma_macer = 0.5
beta_macer = 16

tf.set_random_seed(1234)
sess = tf.Session(config=config)

idx_8_val = np.argwhere(np.argmax(Y_val,1) == target).flatten()
X_val = X_val[idx_8_val]
Y_val = Y_val[idx_8_val]

idx_8_train = np.argwhere(np.argmax(Y_train,1) == target).flatten()
X_poisoned = np.array(X_train[idx_8_train])
Y_poisoned = np.array(Y_train[idx_8_train])
poisoned_points = len(X_poisoned)

X_train = np.delete(X_train, idx_8_train, 0)
Y_train = np.delete(Y_train, idx_8_train, 0)

x_train_tf = tf.placeholder(tf.float32, shape=(batch_size_clean, height, width, nch))
y_train_tf = tf.placeholder(tf.float32, shape=(None, nclass))

x_val_tf = tf.placeholder(tf.float32, shape=(val_batch_size, height, width, nch))

y_val_tf = tf.placeholder(tf.float32, shape=(None, nclass))
y_val_class_tf = tf.placeholder(tf.int32, shape=(None))

x_test_tf = tf.placeholder(tf.float32, shape=(None, height, width, nch))
y_test_tf = tf.placeholder(tf.float32, shape=(None, nclass))

x_poisoned_tf = tf.placeholder(tf.float32, shape=(None, height, width, nch))
y_poisoned_tf = tf.placeholder(tf.float32, shape=(None, nclass))

x_original_tf = tf.placeholder(tf.float32, shape=(None, height, width, nch))

input_shape = X_test.shape[1:]
n = 3
version = 1
if version == 1:
    depth = n * 6 + 2
elif version == 2:
    depth = n * 9 + 2

# Model parameters
with tf.variable_scope('test_model', reuse=False):    
    test_model = resnet_v1(input_shape=input_shape, depth=depth)

var_test = test_model.trainable_weights         
saver_model_test = tf.train.Saver(var_test, max_to_keep = None)

with tf.variable_scope('train_model', reuse=False):    
    train_model = resnet_v1(input_shape=input_shape, depth=depth)
        
var_train = train_model.trainable_weights   
saver_model_train = tf.train.Saver(var_train, max_to_keep = None)   

bl_poisoning = bilevel_poisoning(sess, x_train_tf, x_val_tf, x_test_tf, x_poisoned_tf, x_original_tf, y_train_tf, y_val_tf, y_val_class_tf, y_test_tf, y_poisoned_tf,
                                 batch_size_poisoned, height, width, nch, nclass, val_batch_size, batch_size_clean,
                                 k_macer, sigma_macer, beta_macer,
                                 train_model, test_model, 
                                 var_train, var_test,
                                 sig, lr_v)

sess.run(tf.global_variables_initializer())

X_poisoned_orig = np.array(X_poisoned)

idx_8 = np.argwhere(np.argmax(Y_test,1) == target).flatten()
X_8 = X_test[idx_8]
Y_8 = Y_test[idx_8]

print("\n\nBilevel Training")
for epoch in range(1, nepochs+1):
    
    if epoch%10 == 0:
        np.save("data/X_poisoned_radius_gaussianaug_"+str(sigma_macer)+"_"+str(target)+".npy", X_poisoned)
        np.save("data/Y_poisoned_radius_gaussianaug_"+str(sigma_macer)+"_"+str(target)+".npy", Y_poisoned)
        print("Reinit")
        sess.run(tf.global_variables_initializer())
        
    nb_batches = int(np.floor(len(X_train)/batch_size_clean))
    ind_shuf = np.arange(len(X_train))
    np.random.shuffle(ind_shuf)
        
    for batch in range(nb_batches):
        
        ind_batch = range(batch_size_clean*batch, min(batch_size_clean*(1+batch), len(X_train)))
        ind_tr = ind_shuf[ind_batch]
        
        ind_val = np.random.choice(len(X_val), size=val_batch_size, replace=False)
        ind_poisoned = np.random.choice(len(X_poisoned), size=batch_size_poisoned, replace=False)
    
        fval3, fval4, fval, gval, hval, new_X_poisoned = bl_poisoning.train(X_train[ind_tr], Y_train[ind_tr], X_val[ind_val], Y_val[ind_val], X_poisoned[ind_poisoned], Y_poisoned[ind_poisoned], X_poisoned_orig[ind_poisoned], lr_u, lr_v, lr_p, niter1, niter2)

        delta = new_X_poisoned - X_poisoned_orig[ind_poisoned]
        delta = np.clip(delta, -eps, eps)
        X_poisoned[ind_poisoned] = np.array(X_poisoned_orig[ind_poisoned] + delta)
    
    if epoch % 1 == 0:
        print("epoch = ", epoch, "f3:", fval3, "f4:", fval4, "f:", fval, "g:", gval, "hval:", hval)
        test_acc = bl_poisoning.eval_accuracy(X_test, Y_test, False)
        train_acc = bl_poisoning.eval_accuracy(np.concatenate([X_train, X_poisoned]), np.concatenate([Y_train, Y_poisoned]), True)
        val_acc = bl_poisoning.eval_accuracy(X_val, Y_val, False)
        print("LL:", train_acc, "Val:", val_acc, "Test:", test_acc, "8:", bl_poisoning.eval_accuracy(X_8, Y_8, False))
        print("\n")            