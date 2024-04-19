"""Main A3C agent.
Some codes are inspired by 
https://medium.com/emergent-future/simple-reinforcement-learning-with-tensorflow-part-8-asynchronous-actor-critic-agents-a3c-c88f72a5e9f2
"""
import os
import threading
import time
import gym
import eplus_env
import numpy as np
import tensorflow as tf
from multiprocessing import Value, Lock
from keras import backend as K

from util.logger import Logger
from a3c_v0_2.objectives import a3c_loss
from a3c_v0_2.a3c_network import A3C_Network
from a3c_v0_2.actions import action_map
from a3c_v0_2.action_limits import stpt_limits
from a3c_v0_2.preprocessors import HistoryPreprocessor, process_raw_state_cmbd, get_legal_action, get_reward
from a3c_v0_2.utils import init_variables, get_hard_target_model_updates, get_uninitialized_variables
from a3c_v0_2.state_index import *
from a3c_v0_2.a3c_eval import A3CEval_multiagent, A3CEval
from a3c_v0_2.buildingOptStatus import BuildingWeekdayPatOpt

ACTION_MAP = action_map;
STPT_LIMITS = stpt_limits;
LOG_LEVEL = 'INFO';
LOG_FMT = "[%(asctime)s] %(name)s %(levelname)s:%(message)s";

class A3CThread:
    """
    The thread worker of the A3C algorithm. 
    """
    
    def __init__(self, graph, scope_name, global_name, state_dim, action_size,
                 net_length, vloss_frac, ploss_frac, hregu_frac, shared_optimizer,
                 clip_norm, global_train_step, window_len, init_epsilon,
                 end_epsilon, decay_steps):
        """
        Constructor.
        
        Args:
            graph: tf.Graph
                The tensorflow computation graph.
            scope_name: String
                The scope name of this thread.
            global_name: String
                The global network scope name. 
            state_dim: int 
                The state dimension. It should be the dimension of the raw 
                observation of the environment plus 2 (time info). 
            action_size: int 
                Number of action choices. 
            net_length: int
                The number of layers in the neural network.
            vloss_frac: float
                Used for constructing the loss operation. The fraction to the 
                value loss. 
            ploss_frac: float
                Used for constructing the loss operation. The fraction to the 
                policy loss.
            hregu_frac: float
                Used for constructing the loss operation. The fraction to the 
                entropy regulation in the loss function.
            shared_optimizer: tf.train.Optimizer
                The tensorflow train optimizer.
            clip_norm: float
                Used for gradient clipping.
            global_train_step: tf.Variable
                A shared tensorflow variable to record the global training steps.
            window_len: int 
                The window length to include the history state into the state
                representation. 
        
        """
        
        ###########################################
        ### Create the policy and value network ###
        ###########################################
        network_state_dim = state_dim * window_len;
        self._a3c_network = A3C_Network(graph, scope_name, network_state_dim, 
                                        action_size, net_length);
        self._policy_pred = self._a3c_network.policy_pred;
        self._value_pred = self._a3c_network.value_pred;
        self._state_placeholder = self._a3c_network.state_placeholder;
        self._keep_prob = self._a3c_network.keep_prob;
        self._shared_layer = self._a3c_network.shared_layer;
        
        with graph.as_default(), tf.name_scope(scope_name):
        
        #################################
        ### Create the loss operation ###
        #################################
            # Generate placeholders state and "true" q values
            self._q_true_placeholder = tf.placeholder(tf.float32,
                                                      shape=(None, 1),
                                                      name='q_true_pl');
            # Generate the tensor for one hot policy probablity
            self._action_idx_placeholder = tf.placeholder(tf.uint8,
                                                          shape=(None),
                                                          name='action_idx_pl');
            pi_one_hot = tf.reduce_sum((tf.one_hot(self._action_idx_placeholder,
                                                   action_size) * 
                                        self._policy_pred),
                                        1, True); 
            self._pi_one_hot = pi_one_hot;
            # Add to the Graph the Ops for loss calculation.
            loss = a3c_loss(self._q_true_placeholder, self._value_pred, 
                                  self._policy_pred, pi_one_hot, vloss_frac, 
                                  ploss_frac, hregu_frac);
            self._loss = loss;
        
        #####################################
        ### Create the training operation ###
        #####################################
            # Add a scalar summary for the snapshot loss.
            self._loss_summary = tf.summary.scalar('loss', loss)
            # Compute the gradients
            local_vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, 
                                           scope_name);
            grads_and_vars = shared_optimizer.compute_gradients(loss, local_vars);
            grads = [item[0] for item in grads_and_vars]; # Need only the gradients
            grads, grad_norms = tf.clip_by_global_norm(grads, clip_norm) 
                                                          # Grad clipping
            self._grads = grads;
            # Apply local gradients to global network
            global_vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, 
                                            global_name);
            self._train_op = shared_optimizer.apply_gradients(
                                                    zip(grads,global_vars),
                                                    global_step=global_train_step);
            
        ######################################
        ### Create local network update op ###
        ######################################
        self._local_net_update = get_hard_target_model_updates(graph,
                                                               scope_name, 
                                                               global_name);
        
        #####################################################
        self._network_state_dim = network_state_dim;
        self._window_len = window_len;
        self._histProcessor = HistoryPreprocessor(window_len);
        self._scope_name = scope_name;
        self._graph = graph;
        self._grad_norms = grad_norms;
        self._action_size = action_size;
        self._epsilon_decay_delta = (init_epsilon - end_epsilon)/decay_steps;
        self._e_greedy = init_epsilon;
        
            
    def train(self, sess, t_max, env_name, coordinator, global_counter, global_lock, 
              gamma, e_weight, p_weight, save_freq, log_dir, global_saver, 
              global_summary_writer, T_max, global_agent_eval_list, eval_freq, 
              global_res_list, reward_mode, action_space_name, dropout_prob,
              ppd_penalty_limit):
        """
        The function that the thread worker works to train the networks.
        
        Args:
            sess: tf.Session
                The shared session of tensorflow.
            t_max: int 
                The interaction number with the environment before performing
                one training. 
            env_name: String 
                The environment name.
            coordinator: tf.train.Coordinator
                The shared coordinator for multithreading training.
            global_counter: python multiprocessing.Value
                The shared counter. 
            global_lock: python threading.Lock
                The shared thread lock.
            gamma: float
                The discount rate.
            e_weight: float
                Used for constructing reward. The weight to the HVAC energy.
            p_weight: float
                Used for constructing reward. The weight to the PPD. 
            save_freq: int 
                The frequency to save the global network regarding the global
                counter.
            log_dir: String
                The directory to save the global network.
            global_saver: tf.Saver
                The global saver object to save the network.
            local_logger: Logger object.
                The local Logger object for logging. 
            global_summary_writer: tf.summary.FileWriter
                The global FileWriter object to save the summary output.
            T_max: int 
                The global maximum number of interactions with the environment.
            global_agent_eval_list: list of A3CEval
                A shared A3C evaluation object list. 
            eval_freq: int 
                The evaluation frequency regarding the global training step.
            ppd_penalty_limit: float
                Larger than ppd_penalty_limit PPD will be changed to 1.0.
                
        """
        action_space = ACTION_MAP[action_space_name];
        self._local_logger = Logger().getLogger('A3C_Worker-%s'
                                    %(threading.current_thread().getName()),
                                              LOG_LEVEL, LOG_FMT, log_dir + '/main.log');
        self._local_logger.info('Local worker starts!')
        t = 0;
        t_st = 0;
        # Create the thread specific environment
        env = gym.make(env_name);
        # Prepare env-related information
        env_st_yr = env.start_year;
        env_st_mn = env.start_mon;
        env_st_dy = env.start_day;
        env_st_wd = env.start_weekday;
        env_state_limits = env.min_max_limits;
        env_state_limits.insert(0, (0, 23)); # Add hour limit
        env_state_limits.insert(0, (0, 6)); # Add weekday limit
        pcd_state_limits = np.transpose(env_state_limits);
        # Reset the env
        time_this, ob_this_raw, is_terminal = env.reset();
        # Process and normalize the raw observation
        ob_this_prcd = process_raw_state_cmbd(ob_this_raw, [time_this], env_st_yr, 
                                              env_st_mn, env_st_dy, env_st_wd, 
                                              pcd_state_limits); # 1-D list
        # Get the history stacked state
        ob_this_hist_prcd = self._histProcessor.\
                            process_state_for_network(ob_this_prcd) # 2-D array
        # Create an object for deciding building operation status
        bld_opt = BuildingWeekdayPatOpt(env_st_yr, env_st_mn, env_st_dy, env_st_wd);
        while not coordinator.should_stop():
            # Synchronize local network parameters with 
            # the global network parameters
            #    print (self._scope_name, 'global  vars', sess.run(tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, 
            #                                'global')))
            #    print (self._scope_name, 'local vars', sess.run(tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, 
            #                                self._scope_name)));
            sess.run(self._local_net_update);   
            #with self._graph.as_default():
             #   print (self._scope_name, 'local vars after update', sess.run(tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, 
              #                              self._scope_name)));
            # Reset the counter
            t_st = t;
            # Interact with env
            trajectory_list = []; # A list of (s_t, a_t, r_t) tuples
            while (not is_terminal) and (t - t_st != t_max):
                self._local_logger.debug('The processed stacked state at %0.04f '
                                         'is %s.'%(time_this, str(ob_this_hist_prcd)));
                # Get the action
                action_raw_idx = self._select_sto_action(ob_this_hist_prcd, sess,
                                                         self._e_greedy, dropout_prob = dropout_prob); ####DEBUG FOR DROPOUT
                action_raw_tup = action_space[action_raw_idx];
                cur_htStpt = ob_this_raw[HTSP_RAW_IDX];
                cur_clStpt = ob_this_raw[CLSP_RAW_IDX];
                action_stpt_prcd, action_effec = get_legal_action(
                                                        cur_htStpt, cur_clStpt, 
                                                    action_raw_tup, STPT_LIMITS);
                action_stpt_prcd = list(action_stpt_prcd);
                # Take the action
                time_next, ob_next_raw, is_terminal = \
                                                env.step(action_stpt_prcd);
                # Process and normalize the raw observation
                ob_next_prcd = process_raw_state_cmbd(ob_next_raw, [time_next], 
                                              env_st_yr, env_st_mn, env_st_dy,
                                              env_st_wd, pcd_state_limits); # 1-D list
                # Get the reward
                normalized_hvac_energy = ob_next_prcd[HVACE_RAW_IDX + 2];
                normalized_ppd = ob_next_prcd[ZPPD_RAW_IDX + 2];
                is_opt = bld_opt.get_is_opt(time_next, ob_next_raw);
                reward_next = get_reward(normalized_hvac_energy, normalized_ppd, 
                                         e_weight, p_weight, reward_mode,
                                         ppd_penalty_limit, is_opt);
                self._local_logger.debug('Environment debug: raw action idx is %d, '
                                         'current heating setpoint is %0.04f, '
                                         'current cooling setpoint is %0.04f, '
                                         'actual action is %s, '
                                         'sim time next is %0.04f, '
                                         'raw observation next is %s, '
                                         'processed observation next is %s, '
                                         'reward next is %0.04f.'
                                         %(action_raw_idx, cur_htStpt, cur_clStpt,
                                           str(action_stpt_prcd), time_next, 
                                           ob_next_raw, ob_next_prcd, reward_next));
                # Get the history stacked state
                ob_next_hist_prcd = self._histProcessor.\
                            process_state_for_network(ob_next_prcd) # 2-D array
                # Remember the trajectory 
                trajectory_list.append((ob_this_hist_prcd, action_raw_idx, 
                                        reward_next)) 
                
                # Update lock counter and global counter, do eval
                t += 1;
                self._update_e_greedy(); # Update the epsilon value
                with global_lock:
                    # Do the evaluation
                    if global_counter.value % eval_freq == 0:
                        self._local_logger.info('Evaluating...');
                        global_res_list.append([global_counter.value]);
                        for global_agent_eval in global_agent_eval_list:
                            eval_res = global_agent_eval.evaluate(self._local_logger, 
                                                              reward_mode, action_space_name,
                                                              ppd_penalty_limit);
                            global_res_list[-1].extend([eval_res[0],eval_res[1]]);
                        np.savetxt(log_dir + '/eval_res_hist.csv', 
                                   np.array(global_res_list), delimiter = ',');
                        self._local_logger.info ('Global step: %d, '
                                           'evaluation results %s'
                                           %(global_counter.value, str(global_res_list[-1])));
                    # Global counter increment
                    global_counter.value += 1;
                # Save the global network variable
                if global_counter.value % save_freq == 0: 
                    checkpoint_file = os.path.join(log_dir, 'model_data/model.ckpt');
                    global_saver.save(sess, checkpoint_file, 
                               global_step=int(global_counter.value));
               

                # ...
                if not is_terminal:
                    ob_this_hist_prcd = ob_next_hist_prcd;
                    ob_this_raw = ob_next_raw;
                    ob_this_prcd = ob_next_prcd;
                else:
                    # Reset the env
                    time_this, ob_this_raw, is_terminal_cp = env.reset();
                    # Process and normalize the raw observation
                    ob_this_prcd = process_raw_state_cmbd(ob_this_raw, [time_this], 
                                              env_st_yr, env_st_mn, env_st_dy,
                                              env_st_wd, pcd_state_limits); # 1-D list
                    # Get the history stacked state
                    self._histProcessor.reset();
                    ob_this_hist_prcd = self._histProcessor.\
                            process_state_for_network(ob_this_prcd) # 2-D array
            # Prepare for the training step
            R = 0 if is_terminal else sess.run(
                            self._value_pred,
                            feed_dict = {self._state_placeholder:ob_this_hist_prcd,
                                         self._keep_prob: 1.0 - dropout_prob})####DEBUG FOR DROPOUT####
            traj_len = len(trajectory_list);
            act_idx_list = np.zeros(traj_len, dtype = np.uint8);
            q_true_list = np.zeros((traj_len, 1));
            state_list = np.zeros((traj_len, self._network_state_dim));
            for i in range(traj_len):
                traj_i_from_last = trajectory_list[traj_len - i - 1]; #(s_t, a_t, r_t);
                R = gamma * R + traj_i_from_last[2];
                act_idx_list[i] = traj_i_from_last[1];
                q_true_list[i, :] = R;
                state_list[i, :] = traj_i_from_last[0];
            # Perform training
            training_feed_dict = {self._q_true_placeholder: q_true_list,
                                  self._state_placeholder: state_list,
                                  self._action_idx_placeholder: act_idx_list,
                                  self._keep_prob: 1.0 - dropout_prob};
            _, loss_res, value_pred = sess.run([self._train_op, self._loss, 
                                                self._value_pred], 
                                   feed_dict = training_feed_dict);
            self._local_logger.debug('Value prediction is %s, R is %s.'
                                     %(str(value_pred), str(q_true_list)));
            # Display and record the loss for this thread
            if (t/t_max) % 200 == 0:
                self._local_logger.info ('Local step %d, global step %d: loss ' 
                                       '%0.04f'%(t, global_counter.value, loss_res));
                # Update the events file.
                summary_str = sess.run(self._loss_summary, 
                                             feed_dict=training_feed_dict)
                global_summary_writer.add_summary(summary_str, t);
                global_summary_writer.flush();
            # ...
            if is_terminal:
                is_terminal = is_terminal_cp;
            # Check whether training should stop
            if global_counter.value > T_max:
                coordinator.request_stop()
        # Safely close the environment
        env.end_env();
            
            
    def _update_e_greedy(self):
        self._e_greedy -= self._epsilon_decay_delta;
        
    def _select_sto_action(self, state, sess, e_greedy, dropout_prob):
        """
        Given a state, run stochastic policy network to give an action.
        
        Args:
            state: np.ndarray, 1*m where m is the state feature dimension.
                Processed normalized state.
            sess: tf.Session.
                The tf session.
        
        Return: int 
            The action index.
        """
        # Random
        uni_rdm_greedy = np.random.uniform();
        if uni_rdm_greedy < e_greedy:
            return np.random.choice(self._action_size);
        # On policy
        softmax_a, shared_layer = sess.run([self._policy_pred, self._shared_layer],
                             feed_dict={self._state_placeholder:state,
                                        self._keep_prob: 1.0 - dropout_prob}) ####DEBUG FOR DROPOUT
        softmax_a = softmax_a.flatten();
        self._local_logger.debug('Policy network output: %s, sum to %0.04f'
                                 %(str(softmax_a), sum(softmax_a)));
        uni_rdm = np.random.uniform(); # Avoid select an action with too small probability
        imd_x = uni_rdm;
        for i in range(softmax_a.shape[-1]):
            imd_x -= softmax_a[i];
            if imd_x <= 0.0:
                selected_act = i;
                return selected_act;
        # Debug
        print ('state ', state);
        print ('Softmax output debug ', softmax_a, 'shared_layer ', shared_layer);
    

class A3CAgent:
    """
    """
    def __init__(self,
                 state_dim,
                 window_len,
                 vloss_frac, 
                 ploss_frac, 
                 hregu_frac,
                 num_threads,
                 learning_rate,
                 rmsprop_decay,
                 rmsprop_momet,
                 rmsprop_epsil,
                 clip_norm,
                 log_dir,
                 init_epsilon,
                 end_epsilon,
                 decay_steps,
                 action_space_name,
                 net_length_global,
                 net_length_local,
                 agt_num,
                 dropout_prob):
        
        self._state_dim = state_dim;
        self._window_len = window_len;
        self._effec_state_dim = state_dim * window_len;
        self._action_size = len(ACTION_MAP[action_space_name]);
        self._vloss_frac = vloss_frac;
        self._ploss_frac = ploss_frac;
        self._hregu_frac = hregu_frac;
        self._num_threads = num_threads;
        self._learning_rate = learning_rate;
        self._rmsprop_decay = rmsprop_decay;
        self._rmsprop_momet = rmsprop_momet;
        self._rmsprop_epsil = rmsprop_epsil;
        self._clip_norm = clip_norm;
        self._log_dir = log_dir
        self._init_epsilon = init_epsilon;
        self._end_epsilon = end_epsilon;
        self._decay_steps = decay_steps;
        self._action_space_name = action_space_name;
        self._net_length_global = net_length_global;
        self._net_length_local = net_length_local;
        self._agt_num = agt_num;
        self._dropout_prob = dropout_prob;
        
    def compile(self, is_warm_start, model_dir, save_scope = 'global'):
        """
        This method sets up the required TF graph and operations.
        
        Args:
            is_warm_start: boolean
                If true, construct the graph from the saved model.
            model_dir: str
                The saved model directory.
            save_scope: str
                Choice of all or global. If all, save all a3c network models
                including the global network and the local workers; if global,
                save just the global network model.
        
        Return:
        
        
        """
        g = tf.Graph();
        # Create the global network
        global_network = A3C_Network(g, 'global', self._effec_state_dim,
                                     self._action_size, self._net_length);
        with g.as_default():
            # Create a shared optimizer
            with tf.name_scope('optimizer'):
                shared_optimizer = tf.train.RMSPropOptimizer(
                                                     self._learning_rate, 
                                                     self._rmsprop_decay,
                                                     self._rmsprop_momet,
                                                     self._rmsprop_epsil)
            # Create a coordinator for multithreading
            coordinator = tf.train.Coordinator();
            # Create a global train step variable to record global steps
            global_train_step = tf.Variable(0, name='global_train_step', 
                                            trainable=False);
            # Init ops
            #init_global_op = init_variables(g, 'global');
            #init_allot_op = init_variables(g, 'RMSProp');
        # Create the thread workers list
        workers = [A3CThread(g, 'worker_%d'%(i), 'global', self._state_dim,
                             self._action_size, self._net_length, self._vloss_frac, 
                             self._ploss_frac, self._hregu_frac, shared_optimizer, 
                             self._clip_norm, global_train_step, self._window_len, 
                             self._init_epsilon, self._end_epsilon, self._decay_steps)
                  for i in range(self._num_threads)];
        # Init global network variables or warm start
        with g.as_default():
            # Create a session for running Ops on the Graph.
            sess = tf.Session()
            # Instantiate a SummaryWriter to output summaries and the Graph.
            summary_writer = tf.summary.FileWriter(self._log_dir, sess.graph)
            # Create a saver for writing training checkpoints
            if save_scope == 'global':
                save_var_list = g.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES,
                                             scope=save_scope)
            if save_scope == 'all':
                save_var_list = None;
            saver = tf.train.Saver(var_list = save_var_list);
            # Init ops
            init_global_all_op = tf.global_variables_initializer();
            if not is_warm_start:
                sess.run(init_global_all_op);
            else:
                saver.restore(sess, model_dir);
        # Graph construction finished. No addiontal elements can be added to 
        # the graph. This is for thread safety.  
        g.finalize(); 
        return (g, sess, coordinator, global_network, workers, summary_writer, 
                saver);

    def test(self, sess, global_network, env_test_name, num_episodes, e_weight, p_weight, 
                reward_mode, test_mode, agent_num, ppd_penalty_limit, log_dir):
        env_test = gym.make(env_test_name);
        if test_mode == 'single':
        	a3c_eval = A3CEval(sess, global_network, env_test, num_episodes, self._window_len, e_weight, p_weight);
        	eval_logger = Logger().getLogger('A3C_Test_Single-%s'%(threading.current_thread().getName()),
                                                 LOG_LEVEL, LOG_FMT, log_dir + '/main.log');
        if test_mode == 'multiple':
        	a3c_eval = A3CEval_multiagent(sess, global_network, env_test, num_episodes, self._window_len, e_weight, p_weight, agent_num)
        	eval_logger = Logger().getLogger('A3C_Test_Multiple-%s'%(threading.current_thread().getName()),
                                                 LOG_LEVEL, LOG_FMT, log_dir + '/main.log');
        
        eval_logger.info("Testing...")
        eval_res = a3c_eval.evaluate(eval_logger, reward_mode, self._action_space_name, ppd_penalty_limit);
        eval_logger.info("Testing finished.")

    def fit(self, sess, coordinator, global_network, workers, 
            global_summary_writer, global_saver, env_name_list, t_max, gamma, 
            e_weight, p_weight, save_freq, T_max, eval_epi_num,
            eval_freq, reward_mode, ppd_penalty_limit):
        """
        """
        threads = [];
        global_counter = Value('d', 0.0);
        global_lock = Lock();
        # Create the env for training evaluation
        global_agent_eval_list = [];
        for env_name in env_name_list:
            env_eval = gym.make(env_name);
            global_agent_eval = A3CEval(sess, global_network, env_eval, eval_epi_num, 
                                    self._window_len, e_weight, p_weight);
            global_agent_eval_list.append(global_agent_eval)

        global_res_list = [];
        thread_counter = 0;
        for worker in workers:
            worker_train = lambda: worker.train(sess, t_max, 
                                                env_name_list[0], 
                                                coordinator, global_counter, 
                                                global_lock, gamma, e_weight,
                                                p_weight, save_freq, self._log_dir, 
                                                global_saver, 
                                                global_summary_writer,T_max,
                                                global_agent_eval_list, eval_freq,
                                                global_res_list,
                                                reward_mode, self._action_space_name, 
                                                self._dropout_prob, ppd_penalty_limit);
            thread = threading.Thread(target = (worker_train));
            thread.start();
            time.sleep(1); # Wait for a while for the env to setup
            threads.append(thread);
            thread_counter += 1;
            
        coordinator.join(threads);
           
