import argparse
import os
import random
import gym
import logging
import multiprocessing
import csv

import numpy as np
import tensorflow as tf

import eplus_env

from util.logger import Logger
from a3c_v0_1.a3c import A3CAgent
from a3c_v0_1.a3c_network import A3C_Network_NN, A3C_Network_LSTM
from pathlib import Path

MODEL_DICT = {'nn': A3C_Network_NN, 'lstm': A3C_Network_LSTM}
STATEDIM_DICT = {'nn': 1, 'lstm': 2}
NAME = 'A3C_AGENT_MAIN'
LOG_LEVEL = 'DEBUG'
LOG_FORMATTER = "[%(asctime)s] %(name)s %(levelname)s:%(message)s";

def get_output_folder(parent_dir, env_name):
    """
    The function give a string name of the folder that the output will be
    stored. It finds the existing folder in the parent_dir with the highest
    number of '-run#', and add 1 to the highest number of '-run#'.

    Parameters
    ----------
    parent_dir: str
      Path of the directory containing all experiment runs.
    env_name: str
      The EnergyPlus environment name. 

    Returns
    -------
    parent_dir/run_dir
      Path to this run's save directory.
    """
    os.makedirs(parent_dir, exist_ok=True)
    experiment_id = 0
    for folder_name in os.listdir(parent_dir):
        if not os.path.isdir(os.path.join(parent_dir, folder_name)):
            continue
        try:
            if folder_name.split('-res')[0] == env_name:
              folder_name = int(folder_name.split('-res')[-1])
              if folder_name > experiment_id:
                  experiment_id = folder_name
        except:
            pass
    experiment_id += 1

    parent_dir = os.path.join(parent_dir, env_name)
    parent_dir = parent_dir + '-res{}'.format(experiment_id)
    return parent_dir


def get_args(): 
    parser = argparse.ArgumentParser(description='Run A3C on EnergyPlus')
    parser.add_argument('--env', default='Eplus-v1', help='EnergyPlus env name')
    parser.add_argument(
        '-o', '--output', default='.', help='Directory to save data to')
    parser.add_argument('--max_interactions', default=15000000, type=int, 
    	help='The max number of interactions with the environment for A3C, default is 15000000.');
    parser.add_argument('--window_len', default=4, type=int, help='The state stacking window length, default is 4.');
    parser.add_argument('--state_dim', default=15, type=int, help='The observation state length of one step, default is 15.');
    parser.add_argument('--forecast_dim', default=15, type=int, help='The total forecast state length, default is 15.');
    parser.add_argument('--gamma', default=0.99);
    parser.add_argument('--v_loss_frac', default=0.5, type=float);
    parser.add_argument('--p_loss_frac', default=1.0, type=float);
    parser.add_argument('--h_regu_frac', default=0.01, nargs='+', type=float, 
                        help='Can be a single float or multiple floats, defining the entropy regularization weight. If multiple '
                             'floats are input, the values will be used for different interaction steps defined in the h_decay_bounds.');
    parser.add_argument('--h_decay_bounds', default=[], nargs='+', type=int, 
                        help='None or 1 to many floats can be input. If multiple h_regu_frac are input, this field must not be None; '
                             'for interaction step 0 to the first h_decay_bound, the first h_regu_frac will be used, so on so forth.');
    parser.add_argument('--num_threads', default=8, type=int,
                        help='The number of threads to be used for the asynchronous'
                        ' training. Default is 8. If -1, then this value equals to'
                        ' the max available CPUs.');
    parser.add_argument('--learning_rate', default=0.0001, type=float);
    parser.add_argument('--learning_rate_decay_rate', default=1.0, type=float);
    parser.add_argument('--learning_rate_decay_steps', default=10000000, type=int);
    parser.add_argument('--is_learning_rate_decay_staircase', default=False, type=bool);
    parser.add_argument('--rmsprop_decay', default=0.99, type=float);
    parser.add_argument('--rmsprop_momet', default=0.0, type=float);
    parser.add_argument('--rmsprop_epsil', default=1e-10, type=float);
    parser.add_argument('--clip_norm', default=5.0, type=float);
    parser.add_argument('--train_freq', default=5, type=int);
    parser.add_argument('--rwd_e_para', default=0.4, type=float,
                        help='Reward weight on HVAC energy consumption, default is 0.4.');
    parser.add_argument('--rwd_p_para', default=0.6, type=float,
                        help='Reward wegith on PPD, default is 0.6.');
    parser.add_argument('--action_space', default='default', type=str, help='The action space name, default is default.');
    parser.add_argument('--save_freq', default=50000, type=int);
    parser.add_argument('--save_max_to_keep', default=5, type=int);
    parser.add_argument('--save_scope', default='all', 
                        help='The tensorflow graph save scope, default is global '
                        'which only saves the global network. Choice is all which '
                        'saves all variables under the graph. ');
    parser.add_argument('--eval_freq', default=1000000, type=int);
    parser.add_argument('--eval_epi_num', default=1, type=int);
    parser.add_argument('--init_e', default = 0.0, type=float);
    parser.add_argument('--end_e', default = 0.0, type=float);
    parser.add_argument('--decay_steps', default = 1000000, type=int);
    parser.add_argument('--dropout_prob', default = 0.0, type=float, help='Deprecated.');
    parser.add_argument('--is_warm_start', default=False, type=bool, 
    	help='This is a bool argument, including this arg makes the algorithm read the trained neural network from the model_dir.');
    parser.add_argument('--model_dir', default='None');
    parser.add_argument('--job_mode', default='Train', type=str,
                        help='The job mode, choice of Train or Test. Default is Train.');
    parser.add_argument('--test_env', nargs='+', type=str);
    parser.add_argument('--test_mode', default='Multiple', type=str, help='The test mode, choice of Single and Multiple. '
    	'Default is Multiple. If Single, the trained agent will control the single zone, else, the trained agent will control '
    	'multiple zones.');
    parser.add_argument('--agent_num', default=5, type=int, help='Used when test_mode is Multiple. Default is 5. This value '
    	'Determines how many zones are controlled by the agent in the testing time.');
    parser.add_argument('--debug_log_prob', default=0.0001, type=float);
    parser.add_argument('--is_greedy_policy', default=False, type=bool)
    parser.add_argument('--activation', default='relu', type=str)
    parser.add_argument('--model_type', default='nn', type=str)
    parser.add_argument('--model_param', nargs='+', type=int)
    parser.add_argument('--isNoisyNet', default=False, type=bool, help='Whether to include NoisyNet (proposed by DeepMind) '
      'in the final linear layers.')
    parser.add_argument('--isNoisyNetEval_rmNoise', default=False, type=bool, help='If NoisyNet is included, whether to remove '
      'noise (set noise to zero) during model evaluation.')
    parser.add_argument('--weight_initer', default='glorot_uniform', type=str, help='Network weight initializer type.')
    parser.add_argument('--sharedNet_type', default='Dense', type=str, help='The shared network layer type.')
    parser.add_argument('--action_repeat_n', default=1, type=int, help='The action repeat times.')
    parser.add_argument('--check_args_only', default=False, type=bool, help='Print the arguments only.')
    parser.add_argument('--eval_env_res_max_keep', default=20, type=int, help='Max keep result number for the evaluation env.')
    parser.add_argument('--is_add_time_to_state', default='True', type=str, help='Is add time to the state.')
    parser.add_argument('--is_r_term_zero', default='True', type=str, help='Is R return zero at terminal')
    return parser;

def effective_main(args, reward_func, rewardArgs, metric_func, train_action_func, eval_action_func, 
                    train_action_limits, eval_action_limits, raw_state_process_func, raw_stateLimit_process_func):
    args.num_threads = multiprocessing.cpu_count() if args.num_threads < 0\
                       else args.num_threads;
    if args.check_args_only:
      with open("args.csv", "w") as args_log_file:
        dict_writer = csv.DictWriter(args_log_file, vars(args).keys())
        dict_writer.writeheader()
        dict_writer.writerow(vars(args))
    else: 
      # Preprocess the arguments
      args.output = get_output_folder(args.output, args.env)
      args.is_add_time_to_state = True if args.is_add_time_to_state.lower() == 'true' else False;
      args.is_r_term_zero = True if args.is_r_term_zero.lower() == 'true' else False;

      tf.gfile.MakeDirs(args.output + '/model_data')
      main_logger = Logger().getLogger(NAME, LOG_LEVEL, LOG_FORMATTER, args.output + '/main.log');
      main_logger.info(args)
      # State size
      stateOneStep_len = args.state_dim # 15 for the raw state dim
      # Create the agent
      a3c_agent = A3CAgent(forecast_len = args.forecast_dim, stateOneStep_len = stateOneStep_len, 
                           window_len = args.window_len,
                           vloss_frac = args.v_loss_frac,
                           ploss_frac = args.p_loss_frac, 
                           hregu_frac = args.h_regu_frac,
                           hregu_decay_bounds = args.h_decay_bounds,
                           num_threads = args.num_threads, 
                           learning_rate_args = [args.learning_rate, args.learning_rate_decay_rate, args.learning_rate_decay_steps, args.is_learning_rate_decay_staircase], 
                           rmsprop_decay = args.rmsprop_decay,
                           rmsprop_momet = args.rmsprop_momet,
                           rmsprop_epsil = args.rmsprop_epsil,
                           clip_norm = args.clip_norm, log_dir = args.output,
                           init_epsilon = args.init_e, end_epsilon = args.end_e, 
                           decay_steps = args.decay_steps,
                           action_space_name = args.action_space,
                           dropout_prob = args.dropout_prob,
                           global_logger = main_logger,
                           activation = args.activation,
                           model_type = MODEL_DICT[args.model_type],
                           model_param = args.model_param,
                           noisyNet = args.isNoisyNet,
                           noisyNetEval_rmNoise = args.isNoisyNetEval_rmNoise,
                           weight_initer = args.weight_initer,
                           prcdState_dim = STATEDIM_DICT[args.model_type]
                           );
      main_logger.info ('Start compiling...')
      (g, sess, coordinator, global_network, workers, global_summary_writer, 
       global_saver) = a3c_agent.compile(args.is_warm_start, args.model_dir, 
                                         args.save_scope, args.save_max_to_keep);
      test_envs = [args.env]
      if args.test_env is not None:
        test_envs.extend(args.test_env);
      if args.job_mode.lower() == "train":
          # Start the training
          main_logger.info ('Start the learning...')
          a3c_agent.fit(sess, coordinator, global_network, workers, 
                        global_summary_writer, global_saver, test_envs, args.train_freq,
                        args.gamma, args.rwd_e_para, args.rwd_p_para, args.save_freq, args.max_interactions,
                        args.eval_epi_num, args.eval_freq, reward_func, rewardArgs, metric_func, train_action_func, eval_action_func,  
                        train_action_limits, eval_action_limits, raw_state_process_func, raw_stateLimit_process_func, 
                        args.debug_log_prob, args.is_greedy_policy, args.action_repeat_n, args.eval_env_res_max_keep,
                        args.is_add_time_to_state, args.is_r_term_zero);

      if args.job_mode.lower() == 'test':
          main_logger.info ('Start the testing...')
          a3c_agent.test(sess, global_network, test_envs, args.eval_epi_num, args.e_weight, 
                         args.p_weight, args.reward_mode, args.test_mode.lower(), args.agent_num, 
                         args.ppd_penalty_limit, args.output, raw_state_process_func, raw_stateLimit_process_func);

