import numpy as np
from a3c_v0_2.actions import action_map
from a3c_v0_2.action_limits import stpt_limits
from a3c_v0_2.preprocessors import HistoryPreprocessor, process_raw_state_cmbd, get_legal_action, get_reward
from a3c_v0_2.state_index_multiagent import *
from a3c_v0_2.buildingOptStatus import BuildingWeekdayPatOpt

ACTION_MAP = action_map;
STPT_LIMITS = stpt_limits;

class A3CEval_multiagent:
    def __init__(self, sess, global_network, env, num_episodes, window_len, 
                 e_weight, p_weight, agent_num):
        self._sess = sess;
        self._global_network = global_network;
        self._env = env;
        self._num_episodes = num_episodes;
        self._histProcessor_list = [HistoryPreprocessor(window_len) for _ in range(agent_num)];
        # Prepare env-related information
        self._env_st_yr = env.start_year;
        self._env_st_mn = env.start_mon;
        self._env_st_dy = env.start_day;
        self._env_st_wd = env.start_weekday;
        env_state_limits = env.min_max_limits;
        env_state_limits.insert(0, (0, 23)); # Add hour limit
        env_state_limits.insert(0, (0, 6)); # Add weekday limit
        self._pcd_state_limits = np.transpose(env_state_limits);
        self._e_weight = e_weight;
        self._p_weight = p_weight;
        self._agent_num = agent_num;
        

    def evaluate(self, local_logger, reward_mode, action_space_name, ppd_penalty_limit):
        """
        """
        action_space = ACTION_MAP[action_space_name];
        episode_counter = 1;
        average_reward = np.zeros(self._agent_num);
        # Reset the env
        time_this, ob_this_raw_all, is_terminal = self._env.reset();
        #ob_this_raw_all[-1] = 0; #print
        # Extract state for each agent
        ob_this_raw_list = [self._get_agent_state(ob_this_raw_all, agent_id = agent_i) for agent_i in range(self._agent_num)];
        # Get the history stacked state for each agent
        ob_this_hist_prcd_list = [];
        for agent_i in range(self._agent_num):
            # Process and normalize the raw observation
            ob_this_raw_agent_i = ob_this_raw_list[agent_i];
            ob_this_prcd_agent_i = process_raw_state_cmbd(ob_this_raw_agent_i, [time_this], 
                                                        self._env_st_yr, self._env_st_mn, 
                                                        self._env_st_dy, self._env_st_wd, 
                                                        self._pcd_state_limits); # 1-D list
            histProcessor_i = self._histProcessor_list[agent_i]; 
            histProcessor_i.reset();
            ob_this_hist_prcd_agent_i = histProcessor_i.process_state_for_network(ob_this_prcd_agent_i) # 2-D array
            ob_this_hist_prcd_list.append(ob_this_hist_prcd_agent_i);
        # Do the eval
        this_ep_reward = np.zeros(self._agent_num);
        # Create an object for deciding building operation status
        bld_opt = BuildingWeekdayPatOpt(self._env_st_yr, self._env_st_mn, self._env_st_dy, self._env_st_wd);
        while episode_counter <= self._num_episodes:
            # Get the action
            action_list = [];
            for agent_i in range(self._agent_num):
                action_raw_idx_i = self._select_sto_action(ob_this_hist_prcd_list[agent_i]);
                action_raw_tup_i = action_space[action_raw_idx_i];
                cur_htStpt_i = ob_this_raw_list[agent_i][HTSP_RAW_IDX];
                cur_clStpt_i = ob_this_raw_list[agent_i][CLSP_RAW_IDX];
                action_stpt_prcd_i, action_effec_i = get_legal_action(cur_htStpt_i, cur_clStpt_i, action_raw_tup_i, STPT_LIMITS);
                action_stpt_prcd_i = list(action_stpt_prcd_i);
                action_list.extend(action_stpt_prcd_i);
            # Perform the action
            time_next, ob_next_raw_all, is_terminal = self._env.step(action_list);
            #ob_next_raw_all[-1] = 0;# print
            # Extract the state for each agent
            ob_next_raw_list = [self._get_agent_state(ob_next_raw_all, agent_id = agent_i) for agent_i in range(self._agent_num)];
            # Process the state and normalize it
            ob_next_prcd_list = [process_raw_state_cmbd(ob_next_raw_agent_i, [time_next], self._env_st_yr, self._env_st_mn, 
                                                        self._env_st_dy, self._env_st_wd, self._pcd_state_limits) \
                                for ob_next_raw_agent_i in ob_next_raw_list];
            # Get the reward
            reward_next_list = [];
            for agent_i in range(self._agent_num):
                ob_next_prcd_i = ob_next_prcd_list[agent_i];
                normalized_hvac_energy_i = ob_next_prcd_i[HVACE_RAW_IDX + 2];
                normalized_ppd_i = ob_next_prcd_i[ZPPD_RAW_IDX + 2];
                is_opt = bld_opt.get_is_opt(time_next, ob_next_raw_list[agent_i]);
                reward_next_i = get_reward(normalized_hvac_energy_i, normalized_ppd_i, self._e_weight, self._p_weight,
                                           reward_mode, ppd_penalty_limit, is_opt);
                reward_next_list.append(reward_next_i);
            this_ep_reward += reward_next_list;
            # Get the history stacked state
            ob_next_hist_prcd_list = [self._histProcessor_list[agent_i].process_state_for_network(ob_next_prcd_list[agent_i])\
                                      for agent_i in range(self._agent_num)] # 2-D array
            # Check whether to start a new episode
            if is_terminal:
                time_this, ob_this_raw_all, is_terminal = self._env.reset();
                # Extract state for each agent
                ob_this_raw_list = [self._get_agent_state(ob_this_raw_all, agent_id = agent_i) for agent_i in range(self._agent_num)];
                # Get the history stacked state for each agent
                ob_this_hist_prcd_list = [];
                for agent_i in range(self._agent_num):
                    # Process and normalize the raw observation
                    ob_this_raw_agent_i = ob_this_raw_list[agent_i];
                    ob_this_prcd_agent_i = process_raw_state_cmbd(ob_this_raw_agent_i, [time_this], 
                                                        self._env_st_yr, self._env_st_mn, 
                                                        self._env_st_dy, self._env_st_wd, 
                                                        self._pcd_state_limits); # 1-D list
                    histProcessor_i = self._histProcessor_list[agent_i]; 
                    histProcessor_i.reset();
                    ob_this_hist_prcd_agent_i = histProcessor_i.process_state_for_network(ob_this_prcd_agent_i) # 2-D array
                    ob_this_hist_prcd_list.append(ob_this_hist_prcd_agent_i);
                # Update the average reward
                average_reward = (average_reward * (episode_counter - 1) + this_ep_reward) / episode_counter;
                local_logger.info('Evaluation: average reward by now is ' + str(average_reward));
                episode_counter += 1;
                this_ep_reward = np.zeros(self._agent_num);
                 
            else:
                time_this = time_next;
                ob_this_hist_prcd_list = ob_next_hist_prcd_list;
                ob_this_raw_list = ob_next_raw_list;
                
        return (average_reward);
    
    def _select_sto_action(self, state):
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
        
        softmax_a = self._sess.run(self._global_network.policy_pred, 
                        feed_dict={self._global_network.state_placeholder:state,
                                   self._global_network.keep_prob: 1.0})\
                        .flatten();
        ### DEBUG
        dbg_rdm = np.random.uniform();
        if dbg_rdm < 0.01:
            print ('softmax', softmax_a)
        uni_rdm = np.random.uniform();
        imd_x = uni_rdm;
        for i in range(softmax_a.shape[-1]):
            imd_x -= softmax_a[i];
            if imd_x <= 0.0:
                return i;

    def _get_agent_state(self, ob_this_raw_all, agent_id):
        ret = ob_this_raw_all[:DIS_RAW_IDX + 1]; # Copy the weather observations
        ret.extend(ob_this_raw_all[DIS_RAW_IDX + ZN_OB_NUM * agent_id + 1: DIS_RAW_IDX + ZN_OB_NUM * agent_id + 1 + ZN_OB_NUM]);
        ret.append(ob_this_raw_all[-1]);
        return ret;

class A3CEval:
    def __init__(self, sess, global_network, env, num_episodes, window_len, 
                 e_weight, p_weight):
        self._sess = sess;
        self._global_network = global_network;
        self._env = env;
        self._num_episodes = num_episodes;
        self._histProcessor = HistoryPreprocessor(window_len);
        # Prepare env-related information
        self._env_st_yr = env.start_year;
        self._env_st_mn = env.start_mon;
        self._env_st_dy = env.start_day;
        self._env_st_wd = env.start_weekday;
        env_state_limits = env.min_max_limits;
        env_state_limits.insert(0, (0, 23)); # Add hour limit
        env_state_limits.insert(0, (0, 6)); # Add weekday limit
        self._pcd_state_limits = np.transpose(env_state_limits);
        self._e_weight = e_weight;
        self._p_weight = p_weight;
        

    def evaluate(self, local_logger, reward_mode, action_space_name, ppd_penalty_limit):
        """
        """
        action_space = ACTION_MAP[action_space_name];
        episode_counter = 1;
        average_reward = 0;
        average_max_ppd = 0;
        # Reset the env
        time_this, ob_this_raw, is_terminal = self._env.reset();
        # Process and normalize the raw observation
        ob_this_prcd = process_raw_state_cmbd(ob_this_raw, [time_this], 
                                              self._env_st_yr, self._env_st_mn, 
                                              self._env_st_dy, self._env_st_wd, 
                                              self._pcd_state_limits); # 1-D list
        # Get the history stacked state
        self._histProcessor.reset();
        ob_this_hist_prcd = self._histProcessor.\
                            process_state_for_network(ob_this_prcd) # 2-D array
        # Create an object for deciding building operation status
        bld_opt = BuildingWeekdayPatOpt(self._env_st_yr, self._env_st_mn, self._env_st_dy, self._env_st_wd);
        # Do the eval
        this_ep_reward = 0;
        this_ep_max_ppd = 0;
        while episode_counter <= self._num_episodes:
            # Get the action
            action_raw_idx = self._select_sto_action(ob_this_hist_prcd);
            action_raw_tup = action_space[action_raw_idx];
            cur_htStpt = ob_this_raw[HTSP_RAW_IDX];
            cur_clStpt = ob_this_raw[CLSP_RAW_IDX];
            action_stpt_prcd, action_effec = get_legal_action(
                                                        cur_htStpt, cur_clStpt, 
                                                    action_raw_tup, STPT_LIMITS);
            action_stpt_prcd = list(action_stpt_prcd);
            # Perform the action
            time_next, ob_next_raw, is_terminal = \
                                                self._env.step(action_stpt_prcd);
            # Process and normalize the raw observation
            ob_next_prcd = process_raw_state_cmbd(ob_next_raw, [time_next], 
                                              self._env_st_yr, self._env_st_mn, 
                                              self._env_st_dy, self._env_st_wd, 
                                              self._pcd_state_limits); # 1-D list
            # Get the reward
            normalized_hvac_energy = ob_next_prcd[HVACE_RAW_IDX + 2];
            normalized_ppd = ob_next_prcd[ZPPD_RAW_IDX + 2];
            is_opt = bld_opt.get_is_opt(time_next, ob_next_raw);
            reward_next = get_reward(normalized_hvac_energy, normalized_ppd, 
                                    self._e_weight, self._p_weight, reward_mode, 
                                    ppd_penalty_limit, is_opt);
            this_ep_reward += reward_next;
            this_ep_max_ppd = max(normalized_ppd if is_opt == True else 0,
                                  this_ep_max_ppd);
            # Get the history stacked state
            ob_next_hist_prcd = self._histProcessor.\
                            process_state_for_network(ob_next_prcd) # 2-D array
            # Check whether to start a new episode
            if is_terminal:
                time_this, ob_this_raw, is_terminal = self._env.reset();
                # Process and normalize the raw observation
                ob_this_prcd = process_raw_state_cmbd(ob_this_raw, [time_this], 
                                              self._env_st_yr, self._env_st_mn, 
                                              self._env_st_dy, self._env_st_wd, 
                                              self._pcd_state_limits); # 1-D list
                # Get the history stacked state
                self._histProcessor.reset();
                ob_this_hist_prcd = self._histProcessor.\
                            process_state_for_network(ob_this_prcd) # 2-D array
                # Update the average reward
                average_reward = (average_reward * (episode_counter - 1) 
                                  + this_ep_reward) / episode_counter;
                average_max_ppd = (average_max_ppd * (episode_counter - 1)
                                  + this_ep_max_ppd) / episode_counter;
                local_logger.info('Evaluation: average reward by now is %0.04f'
                                  ', average max PPD is %0.04f'%(average_reward, 
                                                                 average_max_ppd));
                episode_counter += 1;
                this_ep_reward = 0;
                this_ep_max_ppd = 0;
                 
            else:
                time_this = time_next;
                ob_this_hist_prcd = ob_next_hist_prcd;
                ob_this_raw = ob_next_raw;
                
        return (average_reward, average_max_ppd);
    
    def _select_sto_action(self, state):
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
        
        softmax_a = self._sess.run(self._global_network.policy_pred, 
                        feed_dict={self._global_network.state_placeholder:state,
                                   self._global_network.keep_prob: 1.0})\
                        .flatten();
        ### DEBUG
        dbg_rdm = np.random.uniform();
        if dbg_rdm < 0.01:
            print ('softmax', softmax_a)
        uni_rdm = np.random.uniform();
        imd_x = uni_rdm;
        for i in range(softmax_a.shape[-1]):
            imd_x -= softmax_a[i];
            if imd_x <= 0.0:
                return i;

class A3CEval_NV_Multiagent:
    def __init__(self, sess, global_network, env, num_episodes, window_len, 
                 e_weight, p_weight):
        self._sess = sess;
        self._global_network = global_network;
        self._env = env;
        self._num_episodes = num_episodes;
        self._histProcessor = HistoryPreprocessor(window_len);
        # Prepare env-related information
        self._env_st_yr = env.start_year;
        self._env_st_mn = env.start_mon;
        self._env_st_dy = env.start_day;
        self._env_st_wd = env.start_weekday;
        env_state_limits = env.min_max_limits;
        env_state_limits.insert(0, (0, 23)); # Add hour limit
        env_state_limits.insert(0, (0, 6)); # Add weekday limit
        self._pcd_state_limits = np.transpose(env_state_limits);
        self._e_weight = e_weight;
        self._p_weight = p_weight;
        

    def evaluate(self, local_logger, reward_mode, action_space_name, ppd_penalty_limit):
        """
        """
        action_space = ACTION_MAP[action_space_name];
        episode_counter = 1;
        average_reward = 0;
        average_max_ppd = 0;
        # Reset the env
        time_this, ob_this_raw, is_terminal = self._env.reset();
        # Process and normalize the raw observation
        ob_this_prcd = process_raw_state_cmbd(ob_this_raw, [time_this], 
                                              self._env_st_yr, self._env_st_mn, 
                                              self._env_st_dy, self._env_st_wd, 
                                              self._pcd_state_limits); # 1-D list
        # Get the history stacked state
        self._histProcessor.reset();
        ob_this_hist_prcd = self._histProcessor.\
                            process_state_for_network(ob_this_prcd) # 2-D array
        # Create an object for deciding building operation status
        bld_opt = BuildingWeekdayPatOpt(self._env_st_yr, self._env_st_mn, self._env_st_dy, self._env_st_wd);
        # Do the eval
        this_ep_reward = 0;
        this_ep_max_ppd = 0;
        while episode_counter <= self._num_episodes:
            # Get the action
            action_raw_idx_list = self._select_sto_action(ob_this_hist_prcd);
            agt_num = len(action_raw_idx_list);
            action_raw_tup_list = [action_space[action_raw_idx_list[i]] for i \
                                    in range(agt_num)];
            cur_htStpt_list = [ob_this_raw[HTSP_RAW_IDX + BD_OB_NUM + ZN_OB_NUM * i] for i \
                                    in range(agt_num)];
            cur_clStpt_list = [ob_this_raw[CLSP_RAW_IDX + BD_OB_NUM + ZN_OB_NUM * i] for i \
                                    in range(agt_num);
            action_stpt_prcd_list = [get_legal_action(cur_htStpt_list[i], cur_clStpt_list[i], 
                                    action_raw_tup_list[i], STPT_LIMITS)[0] for i in range(agt_num)];
            action_stpt_prcd_list = np(action_stpt_prcd_list).flatten().tolist();
            # Perform the action
            time_next, ob_next_raw, is_terminal = \
                                                self._env.step(action_stpt_prcd_list);
            # Process and normalize the raw observation
            ob_next_prcd = process_raw_state_cmbd(ob_next_raw, [time_next], 
                                              self._env_st_yr, self._env_st_mn, 
                                              self._env_st_dy, self._env_st_wd, 
                                              self._pcd_state_limits); # 1-D list
            # Get the reward
            normalized_hvac_energy = ob_next_prcd[HVACE_RAW_IDX + 2];
            normalized_ppd_list = [ob_next_prcd[BD_OB_NUM + ZN_OB_NUM * i + ZPPD_RAW_IDX + 2] \
                                   for i in range(agt_num)];
            is_opt = bld_opt.get_is_opt(time_next, ob_next_raw);
            reward_next_list = [get_reward(normalized_hvac_energy, normalized_ppd_list[i], 
                                self._e_weight, self._p_weight, reward_mode, ppd_penalty_limit, 
                                is_opt) for i in range(agt_num)];
            if this_ep_reward == 0:
                this_ep_reward = np.zeros(agt_num);
            if this_ep_max_ppd == 0:
                this_ep_max_ppd = np.zeros(agt_num);
            this_ep_reward += reward_next_list;
            for i in range(agt_num):
                this_ep_max_ppd[i] = max(normalized_ppd_list[i] if is_opt == True else 0,
                                         this_ep_max_ppd[i]);
            # Get the history stacked state
            ob_next_hist_prcd = self._histProcessor.\
                            process_state_for_network(ob_next_prcd) # 2-D array
            # Check whether to start a new episode
            if is_terminal:
                time_this, ob_this_raw, is_terminal = self._env.reset();
                # Process and normalize the raw observation
                ob_this_prcd = process_raw_state_cmbd(ob_this_raw, [time_this], 
                                              self._env_st_yr, self._env_st_mn, 
                                              self._env_st_dy, self._env_st_wd, 
                                              self._pcd_state_limits); # 1-D list
                # Get the history stacked state
                self._histProcessor.reset();
                ob_this_hist_prcd = self._histProcessor.\
                            process_state_for_network(ob_this_prcd) # 2-D array
                # Update the average reward
                if average_reward = 0:
                    average_reward = np.zeros(agt_num);
                if average_max_ppd = 0:
                    average_max_ppd = np.zeros(agt_num);
                average_reward = (average_reward * (episode_counter - 1) 
                                        + this_ep_reward) / episode_counter;
                average_max_ppd = (average_max_ppd * (episode_counter - 1)
                                  + this_ep_max_ppd) / episode_counter;
                local_logger.info('Evaluation: average reward by now is %s'
                                  ', average max PPD is %s'%(average_reward, 
                                                                 average_max_ppd));
                episode_counter += 1;
                this_ep_reward = 0;
                this_ep_max_ppd = 0;
                 
            else:
                time_this = time_next;
                ob_this_hist_prcd = ob_next_hist_prcd;
                ob_this_raw = ob_next_raw;
                
        return (average_reward, average_max_ppd);
    
    def _select_sto_action(self, state):
        """
        Given a state, run stochastic policy network to give an action.
        
        Args:
            state: np.ndarray, 1*m where m is the state feature dimension.
                Processed normalized state.
            sess: tf.Session.
                The tf session.
        
        Return: list 
            List of the action index.
        """
        res = [];
        for policy_pred in self._global_network.policy_pred_list:
            softmax_a_i = self._sess.run(policy_pred, 
                        feed_dict={self._global_network.state_placeholder:state,
                                   self._global_network.keep_prob: 1.0})\
                        .flatten();
            dbg_rdm = np.random.uniform();
            if dbg_rdm < 0.01:
                print ('softmax', softmax_a)
            uni_rdm = np.random.uniform();
            imd_x = uni_rdm;
            for i in range(softmax_a.shape[-1]):
                imd_x -= softmax_a[i];
                if imd_x <= 0.0:
                    res.append(i);
                    break;
        return res;

