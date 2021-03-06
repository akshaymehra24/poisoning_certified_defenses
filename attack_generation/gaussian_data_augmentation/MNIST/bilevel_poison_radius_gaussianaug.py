import tensorflow as tf
import numpy as np
from bilevel_approxgrad import bilevel_approxgrad
from cleverhans.utils_keras import KerasModelWrapper
import tensorflow_probability as tfp

class bilevel_poisoning(object):

    def __init__(self, sess, x_train_tf, x_val_tf, x_test_tf, x_poison_tf, x_original_tf, y_train_tf, y_val_tf, y_val_class_tf, y_test_tf, y_poison_tf,sigma_tf,
                 Npoison, height, width, nch, nclass, val_set_size, Ntrain,
                 k_macer, sigma_macer, beta_macer,  
                 model, test_model, 
                 var_train, var_test,
                 sig, lr_v):
    
        self.sess = sess
        
        self.x_train_tf = x_train_tf
        self.x_val_tf = x_val_tf
        self.x_test_tf = x_test_tf
        self.x_poison_tf = x_poison_tf
        self.x_original_tf = x_original_tf
        
        self.y_train_tf = y_train_tf
        self.y_val_tf = y_val_tf
        self.y_val_class_tf = y_val_class_tf
        self.y_test_tf = y_test_tf
        self.y_poison_tf = y_poison_tf
        
        self.sig = sig
        self.sigma_macer = sigma_macer
        
        self.cls_test_noisy = KerasModelWrapper(model).get_logits(self.x_test_tf)
        self.cls_val = KerasModelWrapper(model).get_logits(self.x_val_tf)
        
        self.Npoison = Npoison
        self.height = height
        self.width = width
        self.nch = nch
        self.nclass = nclass
        
        self.u = tf.get_variable('u', shape=(Npoison, self.height, self.width, self.nch), constraint=lambda t: tf.clip_by_value(t,0,1))
        self.assign_u = tf.assign(self.u, self.x_poison_tf)
        
        self.var_train = var_train
        self.var_test = var_test
        
        self.x_val_tf_reshaped = tf.reshape(self.x_val_tf, [-1, height*width*nch])
        self.repeated_x_val_tf = tf.tile(self.x_val_tf_reshaped, [1, k_macer])
        self.repeated_x_val_tf = tf.reshape(self.repeated_x_val_tf, [-1, height*width*nch])
        self.repeated_x_val_tf = tf.reshape(self.repeated_x_val_tf, [-1, height, width, nch])
        
        self.noise = tf.random.normal(self.repeated_x_val_tf.shape) * self.sigma_macer
        
        self.noisy_inputs = self.repeated_x_val_tf + self.noise
        
        self.outputs = KerasModelWrapper(model).get_logits(self.noisy_inputs)
        self.outputs = tf.reshape(self.outputs, [-1, k_macer, nclass])
        
        #Classificatio Loss
        self.ce_loss_unsmoothed = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(logits=self.cls_val, labels=self.y_val_tf))
        
        # Classification loss on smoothed 
        self.outputs_softmax = tf.reduce_mean(tf.nn.softmax(self.outputs, axis = 2), axis = 1)
        self.log_softmax = tf.math.log(self.outputs_softmax + 1E-10)
        self.ce_loss_smoothed = tf.reduce_mean(tf.reduce_sum(-self.y_val_tf * self.log_softmax, 1))
        
        # Robustness loss
        self.beta_outputs = self.outputs * beta_macer
        self.beta_outputs_softmax = tf.reduce_mean(tf.nn.softmax(self.beta_outputs, axis = 2), axis = 1)
        
        self.top2_score, self.top2_idx  = tf.nn.top_k(self.beta_outputs_softmax, 2)
        
        # Define a single scalar Normal distribution.
        self.tfd = tfp.distributions
        self.dist = self.tfd.Normal(loc=0., scale=1.)
        
        self.correct = tf.where(tf.equal(self.top2_idx[:, 0], self.y_val_class_tf), np.arange(val_set_size), -10 * np.ones(val_set_size))
        mask_1 = self.correct >= 0
        
        alpha = 0.001
        self.robustness_loss_1 = tf.boolean_mask(self.dist.quantile((1 - 2*alpha) * self.top2_score[:,0] + alpha) - self.dist.quantile((1 - 2*alpha) * self.top2_score[:,1] + alpha), mask_1)
        self.robustness_loss = tf.reduce_sum(self.robustness_loss_1 * self.sigma_macer * 0.5)/val_set_size
        
        # Total Loss
        self.f = self.robustness_loss

        self.noise_gp = tf.random.normal(self.u.shape) * self.sigma_macer
        self.noisy_poisons = self.u + self.noise_gp
        self.poison_outputs = KerasModelWrapper(model).get_logits(self.noisy_poisons)
        
        self.noise_gt = tf.random.normal(self.x_train_tf.shape) * self.sigma_macer
        self.noisy_train = self.x_train_tf + self.noise_gt
        self.train_outputs = KerasModelWrapper(model).get_logits(self.noisy_train)
        
        self.ce_loss_poison = tf.reduce_sum(tf.nn.softmax_cross_entropy_with_logits(logits=self.poison_outputs, labels=self.y_poison_tf))
        
        self.ce_loss_train = tf.reduce_sum(tf.nn.softmax_cross_entropy_with_logits(logits=self.train_outputs, labels=self.y_train_tf))
        
        self.g = (self.ce_loss_train + self.ce_loss_poison)/(Npoison + Ntrain)
        
        self.bl = bilevel_approxgrad(sess, self.f, self.g, self.u, self.var_train, self.sig)
        
        #All
        self.noise_all = tf.random.normal(self.x_train_tf.shape) * self.sigma_macer
        self.noisy_inputs_all = self.x_train_tf + self.noise_all 
        
        self.cls_train_all = KerasModelWrapper(test_model).get_logits(self.noisy_inputs_all)
        self.cls_test_all = KerasModelWrapper(test_model).get_logits(self.x_test_tf)
        
        self.loss_all = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(logits=self.cls_train_all, labels=self.y_train_tf))
                
        self.optim_all = tf.train.AdamOptimizer(lr_v)
        self.reset_opt_all = tf.variables_initializer(self.optim_all.variables())
        self.optim_simple_all = self.optim_all.minimize(self.loss_all, var_list=self.var_test)
        
    def reset_v(self):
        for var in self.var_train:
            self.sess.run(var.initializer)
    
    def train(self, x_train, y_train, x_val, y_val, x_poisoned, y_poisoned, x_original, lr_u, lr_v, lr_p, niter1, niter2):
        
        self.sess.run(self.assign_u,feed_dict={self.x_poison_tf:x_poisoned})
        feed_dict={self.x_train_tf:x_train, self.y_train_tf:y_train, self.x_val_tf:x_val, self.y_val_tf:y_val, self.y_val_class_tf:np.argmax(y_val, 1), self.y_poison_tf:y_poisoned, self.x_original_tf:x_original}
        
        self.bl.update_v(feed_dict, lr_v, niter1)
        
        fval, gval, hval = self.bl.update_u(feed_dict, lr_u, lr_p, niter2)
        new_x_poisoned = self.sess.run(self.u)
        
        fval, gval, f3, f4 = self.sess.run([self.f, self.g, self.robustness_loss, self.robustness_loss_1], feed_dict)
        
        return [f3, len(f4), fval, gval, hval, new_x_poisoned]
    
        
    def eval_accuracy(self, x_test, y_test, add_noise):
        batch_size = 1000
        nb_batches = int(len(x_test)/batch_size)
        if len(x_test)%batch_size!=0:
            nb_batches += 1
        
        acc = 0
        for batch in range(nb_batches):
            ind_batch = range(batch_size*batch,min(batch_size*(1+batch), len(x_test)))
            if add_noise:
                noise = np.random.normal(0, 1, x_test[ind_batch].shape) * self.sigma_macer
                pred = self.sess.run(self.cls_test_noisy, {self.x_test_tf:x_test[ind_batch]+noise, self.y_test_tf:y_test[ind_batch]})
            else:
                pred = self.sess.run(self.cls_test_noisy, {self.x_test_tf:x_test[ind_batch], self.y_test_tf:y_test[ind_batch]})
            acc += np.sum(np.argmax(pred,1)==np.argmax(y_test[ind_batch],1))
        acc /= np.float32(len(x_test))
        return acc
    
    def eval_accuracy_averaged(self, x_val, y_val):
        
        acc = 0
        pred = self.sess.run(self.log_softmax, {self.x_val_tf:x_val, self.y_val_tf:y_val})
        acc += np.sum(np.argmax(pred,1)==np.argmax(y_val,1))
        acc /= np.float32(len(x_val))
        return acc
    
