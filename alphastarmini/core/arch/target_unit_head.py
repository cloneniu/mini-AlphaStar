#!/usr/bin/env python
# -*- coding: utf-8 -*-

" Target Unit Head."

import torch
import torch.nn as nn
import torch.nn.functional as F

from alphastarmini.lib import utils as L

from alphastarmini.lib.hyper_parameters import Arch_Hyper_Parameters as AHP
from alphastarmini.lib.hyper_parameters import StarCraft_Hyper_Parameters as SCHP
from alphastarmini.lib.hyper_parameters import Scalar_Feature_Size as SFS

__author__ = "Ruo-Ze Liu"

debug = False


class TargetUnitHead(nn.Module):
    '''
    Inputs: autoregressive_embedding, action_type, entity_embeddings
    Outputs:
        target_unit_logits - The logits corresponding to the probabilities of targeting a unit
        target_unit - The sampled target unit
    '''

    def __init__(self, embedding_size=AHP.entity_embedding_size, 
                 max_number_of_unit_types=SCHP.max_unit_type, 
                 is_sl_training=True, temperature=0.8,
                 original_256=AHP.original_256, original_32=AHP.original_32,
                 max_selected=1, autoregressive_embedding_size=AHP.autoregressive_embedding_size):
        super().__init__()
        self.is_sl_training = is_sl_training
        if not self.is_sl_training:
            self.temperature = temperature
        else:
            self.temperature = 1.0

        self.max_number_of_unit_types = max_number_of_unit_types
        self.func_embed = nn.Linear(max_number_of_unit_types, original_256)  # with relu

        self.conv_1 = nn.Conv1d(in_channels=embedding_size, 
                                out_channels=original_32, kernel_size=1, stride=1,
                                padding=0, bias=True)
        self.fc_1 = nn.Linear(autoregressive_embedding_size, original_256)
        self.fc_2 = nn.Linear(original_256, original_32)

        self.small_lstm = nn.LSTM(original_32, original_32, 1, dropout=0.0, batch_first=True)

        # We mostly target one unit
        self.max_selected = 1

        self.softmax = nn.Softmax(dim=-1)

    def forward(self, autoregressive_embedding, action_type, entity_embeddings, entity_num):
        '''
        Inputs:
            autoregressive_embedding: [batch_size x autoregressive_embedding_size]
            action_type: [batch_size x 1]
            entity_embeddings: [batch_size x entity_size x embedding_size]
            entity_num: [batch_size]
        Output:
            target_unit_logits: [batch_size x max_selected x entity_size]
            target_unit: [batch_size x max_selected x 1]
        '''

        batch_size = entity_embeddings.shape[0]

        # entity_embeddings shape is [batch_size x entity_size x embedding_size]
        entity_size = entity_embeddings.shape[1]

        # `func_embed` is computed the same as in the Selected Units head, 
        # and used in the same way for the query (added to the output of the `autoregressive_embedding` 
        # passed through a linear of size 256).
        unit_types_one_hot = L.action_can_apply_to_entity_types_mask(action_type)

        device = next(self.parameters()).device
        unit_types_one_hot = unit_types_one_hot.to(device)

        # unit_types_mask shape: [batch_size x self.max_number_of_unit_types]
        the_func_embed = F.relu(self.func_embed(unit_types_one_hot))

        # the_func_embed shape: [batch_size x 256]
        print("the_func_embed:", the_func_embed) if debug else None
        print("the_func_embed.shape:", the_func_embed.shape) if debug else None

        # generate the length mask for all entities
        mask = torch.arange(entity_size, device=device).float()
        mask = mask.repeat(batch_size, 1)

        # mask: [batch_size, entity_size]
        mask = mask < entity_num.unsqueeze(dim=1)
        print("mask:", mask) if debug else None
        print("mask.shape:", mask.shape) if debug else None

        # Because we mostly target one unit, we don't need a mask.

        # The query is then passed through a ReLU and a linear of size 32, 
        # and the query is applied to the keys which are created the 
        # same way as in the Selected Units head to get `target_unit_logits`.
        # input : [batch_size x entity_size x embedding_size]
        key = self.conv_1(entity_embeddings.transpose(-1, -2)).transpose(-1, -2)

        # output : [batch_size x entity_size x key_size], note key_size = 32
        print("key:", key) if debug else None
        print("key.shape:", key.shape) if debug else None

        target_unit_logits_list = []
        target_unit_list = []
        hidden = None

        # note: repeated for selecting up to one unit
        max_selected = self.max_selected
        for i in range(max_selected):
            # AlphaStar: The query is then passed through a ReLU and a linear of size 32, 
            # and the query is applied to the keys which are created the same way as in 
            # the Selected Units head to get `target_unit_logits`.
            x = self.fc_1(autoregressive_embedding)
            x = the_func_embed + x
            query = self.fc_2(x).unsqueeze(1)

            # we don't need a lstm now
            # query, hidden = self.small_lstm(x)

            # below is matrix multiply
            # key_shape: [batch_size x entity_size x key_size], note key_size = 32
            # query_shape: [batch_size x seq_len x hidden_size], note hidden_size is also 32, seq_len = 1
            y = torch.bmm(key, query.transpose(-1, -2))

            # new y shape: [batch_size x entity_size]
            y = y.squeeze(-1)

            # fill the entity which should be selected a very large negetive value 
            y = y.masked_fill(~mask, -1e9)

            # target_unit_logits shape: [batch_size x entity_size]
            target_unit_logits = y.div(self.temperature)
            print("target_unit_logits:", target_unit_logits) if debug else None
            print("target_unit_logits.shape:", target_unit_logits.shape) if debug else None

            # target_unit_probs shape: [batch_size x entity_size]
            target_unit_probs = self.softmax(target_unit_logits)
            print("target_unit_probs:", target_unit_probs) if debug else None
            print("target_unit_probs.shape:", target_unit_probs.shape) if debug else None

            # AlphaStar: `target_unit` is sampled from `target_unit_logits` using a multinomial with temperature 0.8.
            # target_unit_id shape: [batch_size x 1]
            target_unit_id = torch.multinomial(target_unit_probs, 1)    
            print("target_unit_id:", target_unit_id) if debug else None
            print("target_unit_id.shape:", target_unit_id.shape) if debug else None

            # note, we add a dimension where is in the seq_one to help
            # we concat to the one : [batch_size x max_selected x ?]
            target_unit_logits_list.append(target_unit_logits.unsqueeze(-2))
            target_unit_list.append(target_unit_id.unsqueeze(-2))

            # Note that since this is one of the two terminal arguments (along 
            # with Location Head, since no action has both a target unit and a 
            # target location), it does not return `autoregressive_embedding`.

        # note: we only select one unit, so return the first one
        # target_unit_logits: [batch_size x max_selected x entity_size]
        target_unit_logits_all = torch.cat(target_unit_logits_list, dim=1)

        # target_units: [batch_size x max_selected x 1]
        target_unit_all = torch.cat(target_unit_list, dim=1)

        # AlphaStar: If `action_type` does not involve targetting units, this head is ignored.
        # target_unit_mask: [batch_size x 1]
        target_unit_mask = L.action_involve_targeting_units_mask(action_type).bool()
        print("target_unit_mask:", target_unit_mask) if debug else None
        print("target_unit_mask.shape:", target_unit_mask.shape) if debug else None

        no_target_unit_mask = ~target_unit_mask.squeeze(dim=1)
        print("no_target_unit_mask:", no_target_unit_mask) if debug else None

        target_unit_logits_all[no_target_unit_mask] = 0.  # a magic number
        target_unit_all[no_target_unit_mask] = entity_size - 1  # None index, the same as -1

        print("target_unit_logits_all:", target_unit_logits_all) if debug else None
        print("target_unit_all:", target_unit_all) if debug else None

        return target_unit_logits_all, target_unit_all


def test():
    action_type_sample = 352  # func: 352/Effect_WidowMineAttack_unit (1/queued [2]; 2/unit_tags [512]; 3/target_unit_tag [512])

    batch_size = 4
    autoregressive_embedding = torch.randn(batch_size, AHP.autoregressive_embedding_size)
    #action_type = torch.randint(low=0, high=SFS.available_actions, size=(batch_size, 1))
    action_type = torch.tensor([[0], [1], [168], [352]])

    entity_embeddings = torch.randn(batch_size, AHP.max_entities, AHP.entity_embedding_size)
    entity_nums = torch.tensor([1, 2, 3, 12])

    target_units_head = TargetUnitHead()

    print("autoregressive_embedding:", autoregressive_embedding) if debug else None
    print("autoregressive_embedding.shape:", autoregressive_embedding.shape) if debug else None

    target_unit_logits, target_unit = \
        target_units_head.forward(autoregressive_embedding, action_type, entity_embeddings, entity_nums)

    if target_unit_logits is not None:
        print("target_unit_logits:", target_unit_logits) if debug else None
        print("target_unit_logits.shape:", target_unit_logits.shape) if debug else None
    else:
        print("target_unit_logits is None!")

    if target_unit is not None:
        print("target_unit:", target_unit) if debug else None
        print("target_unit.shape:", target_unit.shape) if debug else None
    else:
        print("target_unit is None!")

    print("This is a test!") if debug else None


if __name__ == '__main__':
    test()
