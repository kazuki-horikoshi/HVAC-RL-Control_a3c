#!python
"""
This is the entry script for the A3C HVAC control algorithm. 

Run EnergyPlus Environment with Asynchronous Advantage Actor Critic (A3C).

Algorithm taken from https://arxiv.org/abs/1602.01783
'Asynchronous Methods for Deep Reinforcement Learning'

Author: Zhiang Zhang
Last update: Aug 28th, 2017

"""
from main_args import *
from a3c_v0_1.cutomized.reward_funcs import reward_func_dict
from a3c_v0_1.cutomized.action_funcs import act_func_dict
from a3c_v0_1.cutomized.raw_state_processors import raw_state_process_map

def main():
    # Common args
    parser = get_args();
    # Specific args
    parser.add_argument('--violation_penalty_scl', default=10.0, type=float,
                        help='Scale temperature setpoint violation error, default is 10.0.')
    parser.add_argument('--train_act_func', default='cslDxActCool_1', type=str,
                        help='The action function corresponding to the action space, default is cslDxActCool_1')
    parser.add_argument('--eval_act_func', default='cslDxActCool_1', type=str,
                        help='The action function corresponding to the action space, default is cslDxActCool_1')
    parser.add_argument('--reward_func', default='cslDxCool_1', type=str)
    parser.add_argument('--raw_state_prcs_func', default='cslDx_1', type=str)
    args = parser.parse_args();
    # Prepare case specific args
    reward_func = reward_func_dict[args.reward_func]
    rewardArgs = [args.violation_penalty_scl];
    train_action_func = act_func_dict[args.train_act_func][0];
    train_action_limits = act_func_dict[args.train_act_func][1];
    eval_action_func = act_func_dict[args.eval_act_func][0];
    eval_action_limits = act_func_dict[args.eval_act_func][1];
    raw_state_process_func = raw_state_process_map[args.raw_state_prcs_func][0];
    raw_stateLimit_process_func = raw_state_process_map[args.raw_state_prcs_func][1];
    effective_main(args, reward_func, rewardArgs, train_action_func, eval_action_func, train_action_limits, 
                    eval_action_limits, raw_state_process_func, raw_stateLimit_process_func);
        

if __name__ == '__main__':
    main()

"""
if args.act_func == '1':
      action_func = mull_stpt_iw;
      action_limits = act_limits_iw_1;
    elif args.act_func == '2':
      action_func = mull_stpt_oaeTrans_iw;
      action_limits = act_limits_iw_2;
    elif args.act_func == '3':
      action_func = mull_stpt_noExpTurnOffMullOP;
      action_limits = act_limits_iw_2
    elif args.act_func == '4':
      action_func = mull_stpt_directSelect;
      action_limits = act_limits_iw_2
    elif args.act_func == '5':
      action_func = iw_iat_stpt_noExpHeatingOp;
      action_limits = act_limits_iw_3;
    elif args.act_func == '6':
      action_func = iw_iat_stpt_noExpHeatingOp;
      action_limits = act_limits_iw_4;
"""