#!/usr/bin/env python
# -*- coding: utf-8 -*-

" Scalar Encoder."

import numpy as np

import torch
import torch.nn as nn
import torch.nn.functional as F

from alphastarmini.lib.alphastar_transformer import Transformer

from alphastarmini.lib.hyper_parameters import Arch_Hyper_Parameters as AHP
from alphastarmini.lib.hyper_parameters import StarCraft_Hyper_Parameters as SCHP
from alphastarmini.lib.hyper_parameters import Scalar_Feature_Size as SFS

from alphastarmini.lib import utils as L

__author__ = "Ruo-Ze Liu"

debug = False


class ScalarEncoder(nn.Module):
    '''    
    Inputs: scalar_features, entity_list
    Outputs:
        embedded_scalar - A 1D tensor of embedded scalar features
        scalar_context - A 1D tensor of certain scalar features we want to use as context for gating later
    '''

    def __init__(self, n_statistics=10, n_upgrades=SFS.upgrades, 
                 n_action_num=SFS.available_actions, n_units_buildings=SFS.unit_counts_bow, 
                 n_effects=SFS.effects, n_upgrade=SFS.upgrade,
                 n_possible_actions=SFS.last_action_type, 
                 n_delay=SFS.last_delay,
                 n_possible_values=SFS.last_repeat_queued,
                 original_32=AHP.original_32,
                 original_64=AHP.original_64,
                 original_128=AHP.original_128,
                 original_256=AHP.original_256,
                 original_512=AHP.original_512):
        super().__init__()
        self.statistics_fc = nn.Linear(n_statistics, original_64)  # with relu
        self.home_race_fc = nn.Linear(5, original_32)  # with relu, also goto scalar_context
        self.away_race_fc = nn.Linear(5, original_32)  # with relu, also goto scalar_context
        self.upgrades_fc = nn.Linear(n_upgrades, original_128)  # with relu
        self.enemy_upgrades_fc = nn.Linear(n_upgrades, original_128)  # with relu
        self.time_fc = original_64  # a transformer positional encoder

        # additional features
        self.available_actions_fc = nn.Linear(n_action_num, original_64)  # with relu, also goto scalar_context
        self.unit_counts_bow_fc = nn.Linear(n_units_buildings, original_64)  # A bag-of-words unit count from `entity_list`, with relu
        self.mmr_fc = nn.Linear(7, original_64)  # mmr is from 0 to 6 (by divison by 1000), with relu

        self.units_buildings_fc = nn.Linear(n_units_buildings, original_32)  # with relu, also goto scalar_context
        self.effects_fc = nn.Linear(n_effects, original_32)  # with relu, also goto scalar_context
        self.upgrade_fc = nn.Linear(n_upgrade, original_32)  # with relu, also goto scalar_context. What is the difference with upgrades_fc?

        self.before_beginning_build_order = nn.Linear(n_units_buildings, 16)  # without relu
        self.beginning_build_order_transformer = Transformer(d_model=16, d_inner=32,
                                                             n_layers=3, n_head=2, 
                                                             d_k=8, d_v=8, 
                                                             dropout=0.)  # make dropout=0 to make training and testing consistent
        # [20, num_entity_types], into transformer with q,k,v, also goto scalar_context
        self.last_delay_fc = nn.Linear(n_delay, original_64)  # with relu
        self.last_action_type_fc = nn.Linear(n_possible_actions, original_128)  # with relu
        self.last_repeat_queued_fc = nn.Linear(n_possible_values, original_256)  # with relu

        self.fc_1 = nn.Linear(AHP.scalar_encoder_fc1_input, original_512)  # with relu
        self.fc_2 = nn.Linear(AHP.scalar_encoder_fc2_input, original_512)  # with relu

        self.relu = nn.ReLU()

    @classmethod
    def preprocess_tensor(cls, obs, build_order=None):
        scalar_list = []

        player = obs["player"]
        print('player:', player) if debug else None

        # The first is player_id, so we don't need it.
        player_statistics = player[1:]
        print('player_statistics:', player_statistics) if debug else None

        # player_statistics = np.log(player_statistics + 1)
        # print('player_statistics:', player_statistics)

        # agent_statistics = torch.ones(1, 10)
        agent_statistics = torch.tensor(player_statistics, dtype=torch.float).reshape(1, -1)
        print('player_statistics:', agent_statistics) if debug else None

        home_race = torch.zeros(1, 5)
        if "home_race_requested" in obs:
            home_race_requested = obs["home_race_requested"].item()
            print('home_race_requested:', home_race_requested) if debug else None
        else:
            home_race_requested = 0
        assert home_race_requested >= 0 and home_race_requested <= 4
        home_race[0, home_race_requested] = 1
        print('home_race:', home_race) if debug else None

        away_race = torch.zeros(1, 5)
        if "away_race_requested" in obs:
            away_race_requested = obs["away_race_requested"].item()
            print('away_race_requested:', away_race_requested) if debug else None
        else:
            away_race_requested = 0
        assert away_race_requested >= 0 and away_race_requested <= 4
        away_race[0, away_race_requested] = 1
        print('away_race:', away_race) if debug else None

        if "action_result" in obs:
            action_result = obs["action_result"]
            print('action_result:', action_result) if debug else None

        if "alerts" in obs:
            alerts = obs["alerts"]
            print('alerts:', alerts) if debug else None

        # implement the upgrades
        upgrades = torch.zeros(1, SFS.upgrades)
        obs_upgrades = obs["upgrades"]
        print('obs_upgrades:', obs_upgrades) if debug else None
        for u in obs_upgrades:
            assert u >= 0 
            assert u < SFS.upgrades
            upgrades[0, u] = 1

        # question: how to know enemy's upgrades?
        enemy_upgrades = torch.zeros(1, SFS.upgrades)

        # time conver to gameloop
        time = torch.zeros(1, SFS.time)
        game_loop = obs["game_loop"]
        print('game_loop:', game_loop) if debug else None

        time_encoding = torch.tensor(L.unpackbits_for_largenumber(game_loop, num_bits=64), dtype = torch.float).reshape(1, -1)
        print('time_encoding:', time_encoding) if debug else None 
        # note, we use binary encoding here for time
        time = time_encoding
        #time[0, 0] = game_loop

        # implement the available_actions
        # note: if we use raw action, this key doesn't exist
        # the_available_actions = obs["available_actions"] 
        available_actions = torch.zeros(1, SFS.available_actions)

        # implement the unit_counts_bow
        unit_counts_bow = L.calculate_unit_counts_bow(obs)
        print('unit_counts_bow:', unit_counts_bow) if debug else None
        print('torch.sum(unit_counts_bow):', torch.sum(unit_counts_bow)) if debug else None

        # implement the build order
        beginning_build_order = torch.zeros(1, SCHP.count_beginning_build_order, int(SFS.beginning_build_order / SCHP.count_beginning_build_order))
        print('beginning_build_order.shape:', beginning_build_order.shape) if debug else None
        if build_order is not None:
            # implement the beginning_build_order               
            for i, bo in enumerate(build_order):
                if i < 20:
                    assert bo < SFS.unit_counts_bow
                    beginning_build_order[0, i, bo] = 1
            print("beginning_build_order:", beginning_build_order) if debug else None
            print("sum(beginning_build_order):", torch.sum(beginning_build_order).item()) if debug else None

        mmr = torch.zeros(1, SFS.mmr)
        units_buildings = torch.zeros(1, SFS.units_buildings)

        # implement the effects
        effects = torch.zeros(1, SFS.effects)
        # we now use feature_effects to represent it
        feature_effects = obs["feature_effects"]
        print('feature_effects:', feature_effects) if debug else None
        for effect in feature_effects:
            e = effect.effect_id
            assert e >= 0 
            assert e < SFS.effects
            effects[0, e] = 1
        # the raw effects are reserved for use
        raw_effects = obs["raw_effects"]
        print('raw_effects:', raw_effects) if debug else None

        # implement the upgrade
        upgrade = torch.zeros(1, SFS.upgrades)
        for u in obs_upgrades:
            assert u >= 0 
            assert u < SFS.upgrades
            upgrade[0, u] = 1

        last_delay = torch.zeros(1, SFS.last_delay)

        # implement the last action
        # note: if we use raw action, this property is always empty
        last_actions = obs["last_actions"]
        print('last_actions:', last_actions) if debug else None
        last_action_type = torch.zeros(1, SFS.last_action_type)

        last_repeat_queued = torch.zeros(1, SFS.last_repeat_queued)

        scalar_list.append(agent_statistics)
        scalar_list.append(home_race)
        scalar_list.append(away_race)
        scalar_list.append(upgrades)
        scalar_list.append(enemy_upgrades)
        scalar_list.append(time)

        scalar_list.append(available_actions)
        scalar_list.append(unit_counts_bow)
        scalar_list.append(mmr)
        scalar_list.append(units_buildings)
        scalar_list.append(effects)
        scalar_list.append(upgrade)

        scalar_list.append(beginning_build_order)
        scalar_list.append(last_delay)
        scalar_list.append(last_action_type)
        scalar_list.append(last_repeat_queued)

        return scalar_list

    @classmethod
    def preprocess_numpy(cls, obs, build_order=None):
        scalar_list = []

        player = obs["player"]
        print('player:', player) if debug else None

        # The first is player_id, so we don't need it.
        player_statistics = player[1:]
        print('player_statistics:', player_statistics) if debug else None

        # player_statistics = np.log(player_statistics + 1)
        # print('player_statistics:', player_statistics)

        # agent_statistics = torch.ones(1, 10)
        agent_statistics = np.array(player_statistics, dtype=np.float32).reshape(1, -1)
        print('player_statistics:', agent_statistics) if debug else None

        home_race = np.zeros((1, 5))
        if "home_race_requested" in obs:
            home_race_requested = obs["home_race_requested"].item()
            print('home_race_requested:', home_race_requested) if debug else None
        else:
            home_race_requested = 0
        assert home_race_requested >= 0 and home_race_requested <= 4
        home_race[0, home_race_requested] = 1
        print('home_race:', home_race) if debug else None

        away_race = np.zeros((1, 5))
        if "away_race_requested" in obs:
            away_race_requested = obs["away_race_requested"].item()
            print('away_race_requested:', away_race_requested) if debug else None
        else:
            away_race_requested = 0
        assert away_race_requested >= 0 and away_race_requested <= 4
        away_race[0, away_race_requested] = 1
        print('away_race:', away_race) if debug else None

        if "action_result" in obs:
            action_result = obs["action_result"]
            print('action_result:', action_result) if debug else None

        if "alerts" in obs:
            alerts = obs["alerts"]
            print('alerts:', alerts) if debug else None

        # implement the upgrades
        upgrades = np.zeros((1, SFS.upgrades))
        obs_upgrades = obs["upgrades"]
        print('obs_upgrades:', obs_upgrades) if debug else None
        for u in obs_upgrades:
            assert u >= 0 
            assert u < SFS.upgrades
            upgrades[0, u] = 1

        # question: how to know enemy's upgrades?
        enemy_upgrades = np.zeros((1, SFS.upgrades))

        # time conver to gameloop
        time = np.zeros((1, SFS.time))
        game_loop = obs["game_loop"]
        print('game_loop:', game_loop) if debug else None

        time_encoding = L.unpackbits_for_largenumber(game_loop, num_bits=64).astype(np.float32).reshape(1, -1)
        print('time_encoding:', time_encoding) if debug else None 
        # note, we use binary encoding here for time
        time = time_encoding
        #time[0, 0] = game_loop

        # TODO: implement the available_actions
        # note: if we use raw action, this key doesn't exist
        # the_available_actions = obs["available_actions"] 
        # print('the_available_actions:', the_available_actions) if debug else None
        available_actions = np.zeros((1, SFS.available_actions))

        # implement the unit_counts_bow
        unit_counts_bow = L.calculate_unit_counts_bow_numpy(obs)
        print('unit_counts_bow:', unit_counts_bow) if debug else None
        print('torch.sum(unit_counts_bow):', np.sum(unit_counts_bow)) if debug else None

        # implement the build order
        beginning_build_order = np.zeros((1, SCHP.count_beginning_build_order, int(SFS.beginning_build_order / SCHP.count_beginning_build_order)))
        print('beginning_build_order.shape:', beginning_build_order.shape) if debug else None
        if build_order is not None:
            # implement the beginning_build_order               
            for i, bo in enumerate(build_order):
                if i < 20:
                    assert bo < SFS.unit_counts_bow
                    beginning_build_order[0, i, bo] = 1
            print("beginning_build_order:", beginning_build_order) if debug else None
            print("sum(beginning_build_order):", np.sum(beginning_build_order).item()) if debug else None

        mmr = np.zeros((1, SFS.mmr))
        units_buildings = np.zeros((1, SFS.units_buildings))

        # implement the effects
        effects = np.zeros((1, SFS.effects))
        # we now use feature_effects to represent it
        feature_effects = obs["feature_effects"]
        print('feature_effects:', feature_effects) if debug else None
        for effect in feature_effects:
            e = effect.effect_id
            assert e >= 0 
            assert e < SFS.effects
            effects[0, e] = 1
        # the raw effects are reserved for use
        raw_effects = obs["raw_effects"]
        print('raw_effects:', raw_effects) if debug else None

        # implement the upgrade
        upgrade = np.zeros((1, SFS.upgrades))
        for u in obs_upgrades:
            assert u >= 0 
            assert u < SFS.upgrades
            upgrade[0, u] = 1

        last_delay = np.zeros((1, SFS.last_delay))

        # TODO: implement the last action
        # note: if we use raw action, this property is always empty
        last_actions = obs["last_actions"]
        print('last_actions:', last_actions) if debug else None
        last_action_type = np.zeros((1, SFS.last_action_type))
        last_repeat_queued = np.zeros((1, SFS.last_repeat_queued))

        scalar_list.append(agent_statistics)
        scalar_list.append(home_race)
        scalar_list.append(away_race)
        scalar_list.append(upgrades)
        scalar_list.append(enemy_upgrades)
        scalar_list.append(time)

        scalar_list.append(available_actions)
        scalar_list.append(unit_counts_bow)
        scalar_list.append(mmr)
        scalar_list.append(units_buildings)
        scalar_list.append(effects)
        scalar_list.append(upgrade)

        scalar_list.append(beginning_build_order)
        scalar_list.append(last_delay)
        scalar_list.append(last_action_type)
        scalar_list.append(last_repeat_queued)

        return scalar_list

    def forward(self, scalar_list):
        [agent_statistics, home_race, away_race, upgrades, enemy_upgrades, time, available_actions, unit_counts_bow,
         mmr, units_buildings, effects, upgrade, beginning_build_order, last_delay, last_action_type,
         last_repeat_queued] = scalar_list

        embedded_scalar_list = []
        scalar_context_list = []

        # agent_statistics: Embedded by taking log(agent_statistics + 1) and passing through a linear of size 64 and a ReLU

        print('agent_statistics:', agent_statistics) if debug else None
        print('agent_statistics+1:', agent_statistics + 1) if debug else None
        print('torch.log(agent_statistics + 1):', torch.log(agent_statistics + 1)) if debug else None

        the_log_statistics = torch.log(agent_statistics + 1)
        if torch.isnan(the_log_statistics).any():
            print('Find NAN the_log_statistics !', the_log_statistics)
            eps = 1e-9
            the_log_statistics = torch.log(self.relu(agent_statistics + 1) + eps)

            if torch.isnan(the_log_statistics).any():
                print('Find NAN the_log_statistics !', the_log_statistics)
                the_log_statistics = torch.ones_like(agent_statistics)

        x = F.relu(self.statistics_fc(the_log_statistics))
        embedded_scalar_list.append(x)

        # race: Both races are embedded into a one-hot with maximum 5, and embedded through a linear of size 32 and a ReLU.
        x = F.relu(self.home_race_fc(home_race))
        embedded_scalar_list.append(x)
        # The embedding is also added to `scalar_context`.
        scalar_context_list.append(x)

        # race: Both races are embedded into a one-hot with maximum 5, and embedded through a linear of size 32 and a ReLU.
        x = F.relu(self.away_race_fc(away_race))
        # TODO: During training, the opponent's requested race is hidden in 10% of matches, to simulate playing against the Random race.
        embedded_scalar_list.append(x)
        # The embedding is also added to `scalar_context`.
        scalar_context_list.append(x)
        # TODO: If we don't know the opponent's race (either because they are random or it is hidden), 
        # we add their true race to the observation once we observe one of their units.

        # upgrades: The boolean vector of whether an upgrade is present is embedded through a linear of size 128 and a ReLU
        x = F.relu(self.upgrades_fc(upgrades))
        embedded_scalar_list.append(x)

        # enemy_upgrades: Embedded the same as upgrades
        x = F.relu(self.enemy_upgrades_fc(enemy_upgrades))
        embedded_scalar_list.append(x)

        # TODO: time: A transformer positional encoder encoded the time into a 1D tensor of size 64
        x = time
        embedded_scalar_list.append(x)

        # available_actions: From `entity_list`, we compute which actions may be available and which can never be available. 
        # For example, the agent controls a Stalker and has researched the Blink upgrade, 
        # then the Blink action may be available (even though in practice it may be on cooldown). 
        # The boolean vector of action availability is passed through a linear of size 64 and a ReLU.
        x = F.relu(self.available_actions_fc(available_actions))
        embedded_scalar_list.append(x)
        # The embedding is also added to `scalar_context`
        scalar_context_list.append(x)

        # unit_counts_bow: A bag-of-words unit count from `entity_list`. 
        # The unit count vector is embedded by square rooting, passing through a linear layer, and passing through a ReLU
        x = F.relu(self.unit_counts_bow_fc(unit_counts_bow))
        embedded_scalar_list.append(x)

        # mmr: During supervised learning, this is the MMR of the player we are trying to imitate. Elsewhere, this is fixed at 6200. 
        # MMR is mapped to a one-hot of min(mmr / 1000, 6) with maximum 6, then passed through a linear of size 64 and a ReLU
        x = F.relu(self.mmr_fc(mmr))
        embedded_scalar_list.append(x)

        # cumulative_statistics: The cumulative statistics (including units, buildings, effects, and upgrades) are preprocessed 
        # into a boolean vector of whether or not statistic is present in a human game. 
        # That vector is split into 3 sub-vectors of units/buildings, effects, and upgrades, 
        # and each subvector is passed through a linear of size 32 and a ReLU, and concatenated together.
        # The embedding is also added to `scalar_context`
        x = F.relu(self.units_buildings_fc(units_buildings))
        embedded_scalar_list.append(x)
        scalar_context_list.append(x)

        x = F.relu(self.effects_fc(effects))
        embedded_scalar_list.append(x)
        scalar_context_list.append(x)

        x = F.relu(self.upgrade_fc(upgrade))
        embedded_scalar_list.append(x)
        scalar_context_list.append(x)

        # beginning_build_order: The first 20 constructed entities are converted to a 2D tensor of size 
        # [20, num_entity_types], concatenated with indices and the binary encodings 
        # (as in the Entity Encoder) of where entities were constructed (if applicable). 
        # The concatenation is passed through a transformer similar to the one in the entity encoder, 
        # but with keys, queries, and values of 8 and with a MLP hidden size of 32. 
        # The embedding is also added to `scalar_context`.
        print("beginning_build_order:", beginning_build_order) if debug else None
        print("beginning_build_order.shape:", beginning_build_order.shape) if debug else None

        x = self.beginning_build_order_transformer(self.before_beginning_build_order(beginning_build_order))
        print("x:", x) if debug else None
        print("x.shape:", x.shape) if debug else None

        x = x.reshape(x.shape[0], SCHP.count_beginning_build_order * 16)
        print("x:", x) if debug else None
        print("x.shape:", x.shape) if debug else None

        embedded_scalar_list.append(x)
        scalar_context_list.append(x)

        # last_delay: The delay between when we last acted and the current observation, in game steps. 
        # This may be different from what we requested due to network latency or APM limits. 
        # It is encoded into a one-hot with maximum 128 and passed through a linear of size 64 and a ReLU
        x = F.relu(self.last_delay_fc(last_delay))
        embedded_scalar_list.append(x)

        # last_action_type: The last action type is encoded into a one-hot with maximum equal 
        # to the number of possible actions, and passed through a linear of size 128 and a ReLU
        x = F.relu(self.last_action_type_fc(last_action_type))
        embedded_scalar_list.append(x)

        # last_repeat_queued: Some other action arguments (queued and repeat) are one-hots with 
        # maximum equal to the number of possible values for those arguments, 
        # and jointly passed through a linear of size 256 and ReLU
        x = F.relu(self.last_repeat_queued_fc(last_repeat_queued))
        embedded_scalar_list.append(x)

        # for x in embedded_scalar_list:
        #    print('embedded_scalar shape:', x.shape)

        embedded_scalar = torch.cat(embedded_scalar_list, dim=1)
        embedded_scalar_out = F.relu(self.fc_1(embedded_scalar))

        scalar_context = torch.cat(scalar_context_list, dim=1)
        scalar_context_out = F.relu(self.fc_2(scalar_context))

        return embedded_scalar_out, scalar_context_out


def test(debug=False):

    scalar_encoder = ScalarEncoder()

    batch_size = 2
    # dummy scalar list
    scalar_list = []

    agent_statistics = torch.ones(batch_size, SFS.agent_statistics)
    home_race = torch.randn(batch_size, SFS.home_race)
    away_race = torch.randn(batch_size, SFS.away_race)
    upgrades = torch.randn(batch_size, SFS.upgrades)
    enemy_upgrades = torch.randn(batch_size, SFS.upgrades)
    time = torch.randn(batch_size, SFS.time)

    available_actions = torch.randn(batch_size, SFS.available_actions)
    unit_counts_bow = torch.randn(batch_size, SFS.unit_counts_bow)
    mmr = torch.randn(batch_size, SFS.mmr)
    units_buildings = torch.randn(batch_size, SFS.units_buildings)
    effects = torch.randn(batch_size, SFS.effects)
    upgrade = torch.randn(batch_size, SFS.upgrade)

    beginning_build_order = torch.randn(batch_size, SCHP.count_beginning_build_order, 
                                        int(SFS.beginning_build_order / SCHP.count_beginning_build_order))
    last_delay = torch.randn(batch_size, SFS.last_delay)
    last_action_type = torch.randn(batch_size, SFS.last_action_type)
    last_repeat_queued = torch.randn(batch_size, SFS.last_repeat_queued)

    scalar_list.append(agent_statistics)
    scalar_list.append(home_race)
    scalar_list.append(away_race)
    scalar_list.append(upgrades)
    scalar_list.append(enemy_upgrades)
    scalar_list.append(time)

    scalar_list.append(available_actions)
    scalar_list.append(unit_counts_bow)
    scalar_list.append(mmr)
    scalar_list.append(units_buildings)
    scalar_list.append(effects)
    scalar_list.append(upgrade)

    scalar_list.append(beginning_build_order)
    scalar_list.append(last_delay)
    scalar_list.append(last_action_type)
    scalar_list.append(last_repeat_queued)

    embedded_scalar, scalar_context = scalar_encoder.forward(scalar_list)

    print("embedded_scalar:", embedded_scalar) if debug else None
    print("embedded_scalar.shape:", embedded_scalar.shape) if debug else None

    print("scalar_context:", scalar_context) if debug else None
    print("scalar_context.shape:", scalar_context.shape) if debug else None

    if debug:
        print("This is a test!")
