#!/usr/bin/env python
# -*- coding: utf-8 -*-

" Location Head."

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from torch.nn.init import kaiming_uniform, normal

from alphastarmini.lib.hyper_parameters import Arch_Hyper_Parameters as AHP
from alphastarmini.lib.hyper_parameters import MiniStar_Arch_Hyper_Parameters as MAHP

from alphastarmini.lib.hyper_parameters import StarCraft_Hyper_Parameters as SCHP
from alphastarmini.lib.hyper_parameters import Scalar_Feature_Size as SFS

from alphastarmini.lib import utils as L

__author__ = "Ruo-Ze Liu"

debug = False


class ResBlockFiLM(nn.Module):
    # some copy from https://github.com/rosinality/film-pytorch/blob/master/model.py
    def __init__(self, filter_size):
        super().__init__()

        self.conv1 = nn.Conv2d(filter_size, filter_size, kernel_size=[1, 1], stride=1, padding=0)
        self.conv2 = nn.Conv2d(filter_size, filter_size, kernel_size=[3, 3], stride=1, padding=1, bias=False)
        self.bn = nn.BatchNorm2d(filter_size, affine=False)

        self.reset()

    def forward(self, x, gamma, beta):
        out = self.conv1(x)
        resid = F.relu(out)
        out = self.conv2(resid)
        out = self.bn(out)

        gamma = gamma.unsqueeze(2).unsqueeze(3)
        beta = beta.unsqueeze(2).unsqueeze(3)

        out = gamma * out + beta

        out = F.relu(out)
        out = out + resid

        return out

    def reset(self):
        # deprecated, should try to find others
        # kaiming_uniform(self.conv1.weight)
        # self.conv1.bias.data.zero_()
        # kaiming_uniform(self.conv2.weight)
        pass


class FiLM(nn.Module):
    # some copy from https://github.com/rosinality/film-pytorch/blob/master/model.py
    def __init__(self, n_resblock=4, conv_hidden=128, gate_size=1024):
        super().__init__()
        self.n_resblock = n_resblock
        self.conv_hidden = conv_hidden

        self.resblocks = nn.ModuleList()
        for i in range(n_resblock):
            self.resblocks.append(ResBlockFiLM(conv_hidden))

        self.film_net = nn.Linear(gate_size, conv_hidden * 2 * n_resblock)

    def reset(self):
        # deprecated, should try to find others
        # kaiming_uniform(self.film_net.weight)
        # self.film_net.bias.data.zero_()
        pass

    def forward(self, x, gate):
        out = x
        film = self.film_net(gate).chunk(self.n_resblock * 2, 1)

        for i, resblock in enumerate(self.resblocks):
            out = resblock(out, film[i * 2], film[i * 2 + 1])

        return out


class FiLMplusMapSkip(nn.Module):
    # Thanks mostly from https://github.com/metataro/sc2_imitation_learning in spatial_decoder
    def __init__(self, n_resblock=4, conv_hidden=128, gate_size=1024):
        super().__init__()
        self.n_resblock = n_resblock
        self.conv_hidden = conv_hidden

        self.resblocks = nn.ModuleList()
        for i in range(n_resblock):
            self.resblocks.append(ResBlockFiLM(conv_hidden))

        self.film_net = nn.Linear(gate_size, conv_hidden * 2 * n_resblock)

    def reset(self):
        # deprecated, should try to find others
        # kaiming_uniform(self.film_net.weight)
        # self.film_net.bias.data.zero_()
        pass

    def forward(self, x, gate, map_skip):
        out = x
        film = self.film_net(gate).chunk(self.n_resblock * 2, 1)

        for i, resblock in enumerate(self.resblocks):
            out = resblock(out, film[i * 2], film[i * 2 + 1])
            out = out + map_skip[i]

        # TODO: should we add a relu?

        return out


class LocationHead(nn.Module):
    '''
    Inputs: autoregressive_embedding, action_type, map_skip
    Outputs:
        target_location_logits - The logits corresponding to the probabilities of targeting each location
        target_location - The sampled target location
    '''

    def __init__(self, autoregressive_embedding_size=AHP.autoregressive_embedding_size, 
                 output_map_size=SCHP.world_size, is_sl_training=True, 
                 max_map_channels=AHP.location_head_max_map_channels,
                 temperature=0.8):
        super().__init__()
        self.use_improved_one = True

        self.is_sl_training = is_sl_training
        if not self.is_sl_training:
            self.temperature = temperature
        else:
            self.temperature = 1.0

        mmc = max_map_channels
        self.ds_1 = nn.Conv2d(mmc + 4, mmc, kernel_size=1, stride=1,
                              padding=0, bias=True)
        self.film_blocks_num = 4

        if not self.use_improved_one:
            self.film_net = FiLM(n_resblock=self.film_blocks_num, 
                                 conv_hidden=mmc, 
                                 gate_size=autoregressive_embedding_size)
        else:
            self.film_net_mapskip = FiLMplusMapSkip(n_resblock=self.film_blocks_num, 
                                                    conv_hidden=mmc, 
                                                    gate_size=autoregressive_embedding_size)            

        self.us_1 = nn.ConvTranspose2d(mmc, int(mmc / 2), kernel_size=4, stride=2,
                                       padding=1, bias=True)
        self.us_2 = nn.ConvTranspose2d(int(mmc / 2), int(mmc / 4), 
                                       kernel_size=4, stride=2,
                                       padding=1, bias=True)
        self.us_3 = nn.ConvTranspose2d(int(mmc / 4), int(mmc / 8), 
                                       kernel_size=4, stride=2,
                                       padding=1, bias=True)
        self.us_4 = nn.ConvTranspose2d(int(mmc / 8), int(mmc / 16), 
                                       kernel_size=4, stride=2,
                                       padding=1, bias=True)
        self.us_4_original = nn.ConvTranspose2d(int(mmc / 8), 1, 
                                                kernel_size=4, stride=2,
                                                padding=1, bias=True)        

        # note: in mAS, we add a upsampling layer to transfer from 8x8 to 256x256
        self.us_5 = nn.ConvTranspose2d(int(mmc / 16), 1, kernel_size=4, stride=2,
                                       padding=1, bias=True)
        self.output_map_size = output_map_size
        self.softmax = nn.Softmax(dim=-1)

    def forward(self, autoregressive_embedding, action_type, map_skip):    
        '''
        Inputs:
            autoregressive_embedding: [batch_size x autoregressive_embedding_size]
            action_type: [batch_size x 1]
            map_skip: [batch_size x channel x height x width]
        Output:
            target_location_logits: [batch_size x self.output_map_size x self.output_map_size]
            location_out: [batch_size x 2 (x and y)]
        '''

        # AlphaStar: `autoregressive_embedding` is reshaped to have the same height/width as the final skip in `map_skip` 
        # AlphaStar: (which was just before map information was reshaped to a 1D embedding) with 4 channels
        # sc2_imitation_learning: map_skip = list(reversed(map_skip))
        # sc2_imitation_learning: inputs, map_skip = map_skip[0], map_skip[1:]
        map_skip = list(reversed(map_skip))
        x, map_skip = map_skip[0], map_skip[1:]

        print("x.shape:", map_skip.shape) if debug else None
        batch_size = x.shape[0]

        assert autoregressive_embedding.shape[0] == action_type.shape[0]
        assert autoregressive_embedding.shape[0] == x.shape[0]

        reshap_size = x.shape[-1]
        reshape_channels = int(AHP.autoregressive_embedding_size / (reshap_size * reshap_size))

        print("autoregressive_embedding.shape:", autoregressive_embedding.shape) if debug else None
        ar_map = autoregressive_embedding.reshape(batch_size, -1, reshap_size, reshap_size)
        print("ar_map.shape:", ar_map.shape) if debug else None

        # AlphaStar: and the two are concatenated together along the channel dimension,
        # map skip shape: (-1, 128, 16, 16)
        # x shape: (-1, 132, 16, 16)
        x = torch.cat([ar_map, x], dim=1)
        print("x.shape:", x.shape) if debug else None

        # AlphaStar: passed through a ReLU, 
        # AlphaStar: passed through a 2D convolution with 128 channels and kernel size 1,    
        # AlphaStar: then passed through another ReLU.
        x = F.relu(self.ds_1(F.relu(x)))

        if not self.use_improved_one:
            # AlphaStar: The 3D tensor (height, width, and channels) is then passed through a series of Gated ResBlocks 
            # AlphaStar: with 128 channels, kernel size 3, and FiLM, gated on `autoregressive_embedding`  
            # note: FilM is Feature-wise Linear Modulation, please see the paper "FiLM: Visual Reasoning with 
            # a General Conditioning Layer"
            # in here we use 4 Gated ResBlocks, and the value can be changed
            x = self.film_net(x, gate=autoregressive_embedding)

            # x shape (-1, 128, 16, 16)
            # AlphaStar: and using the elements of `map_skip` in order of last ResBlock skip to first.
            x = x + map_skip
        else:
            # Referenced mostly from "sc2_imitation_learning" project in spatial_decoder
            assert len(map_skip) == self.film_blocks_num

            # use the new FiLMplusMapSkip class
            x = self.film_net_mapskip(x, gate=autoregressive_embedding, 
                                      map_skip=map_skip)

            # Compared to AS, we a relu, referred from "sc2_imitation_learning"
            x = F.relu(x)

        # AlphaStar: Afterwards, it is upsampled 2x by each of a series of transposed 2D convolutions 
        # AlphaStar: with kernel size 4 and channel sizes 128, 64, 16, and 1 respectively 
        # AlphaStar: (upsampled beyond the 128x128 input to 256x256 target location selection).
        x = F.relu(self.us_1(x))
        x = F.relu(self.us_2(x))
        x = F.relu(self.us_3(x))

        if AHP == MAHP:
            x = F.relu(self.us_4(x))
            # only in mAS, we need one more upsample step
            # x = F.relu(self.us_5(x))
            # Note: in the final layer, we don't use relu
            x = self.us_5(x)
        else:
            x = self.us_4_original(x)

        # AlphaStar: Those final logits are flattened and sampled (masking out invalid locations using `action_type`, 
        # AlphaStar: such as those outside the camera for build actions) with temperature 0.8 
        # AlphaStar: to get the actual target position.
        # x shape: (-1, 1, 256, 256)
        print('x.shape:', x.shape) if debug else None
        y = x.reshape(batch_size, 1 * self.output_map_size * self.output_map_size)

        device = next(self.parameters()).device

        print("y:", y) if debug else None
        print("y_.shape:", y.shape) if debug else None

        target_location_logits = y.div(self.temperature)
        print("target_location_logits:", target_location_logits) if debug else None
        print("target_location_logits.shape:", target_location_logits.shape) if debug else None

        # AlphaStar: (masking out invalid locations using `action_type`, such as those outside 
        # the camera for build actions)
        # TODO: use action to decide the mask
        if True:
            # referenced from lib/utils.py function of masked_softmax()
            mask = torch.zeros(batch_size, 1 * self.output_map_size * self.output_map_size, device=device)
            mask = L.get_location_mask(mask)
            mask_fill_value = -1e32  # a very small number
            masked_vector = target_location_logits.masked_fill((1 - mask).bool(), mask_fill_value)
            target_location_probs = self.softmax(masked_vector)
        else:
            target_location_probs = self.softmax(target_location_logits)

        location_id = torch.multinomial(target_location_probs, num_samples=1, replacement=True)
        print("location_id:", location_id) if debug else None
        print("location_id.shape:", location_id.shape) if debug else None

        location_out = location_id.squeeze(-1).cpu().numpy().tolist()
        print("location_out:", location_out) if debug else None
        # print("location_out.shape:", location_out.shape) if debug else None

        for i, idx in enumerate(location_id):
            row_number = idx // self.output_map_size
            col_number = idx - self.output_map_size * row_number

            target_location_y = row_number
            target_location_x = col_number
            print("target_location_y, target_location_x", target_location_y, target_location_x) if debug else None
            # note! sc2 and pysc2 all accept the position as [x, y], so x be the first, y be the last!
            # this is not right : location_out[i] = [target_location_y.item(), target_location_x.item()]
            # below is right! so the location point map to the point in the matrix!
            location_out[i] = [target_location_x.item(), target_location_y.item()]

        # AlphaStar: If `action_type` does not involve targetting location, this head is ignored.
        target_location_mask = L.action_involve_targeting_location_mask(action_type)
        # target_location_mask: [batch_size x 1]
        print("target_location_mask:", target_location_mask) if debug else None

        print("location_out:", location_out) if debug else None
        location_out = np.array(location_out)
        print("location_out:", location_out) if debug else None
        location_out = torch.tensor(location_out, device=device)
        print("location_out:", location_out) if debug else None
        print("location_out.shape:", location_out.shape) if debug else None

        target_location_logits = target_location_logits.reshape(-1, self.output_map_size, self.output_map_size)
        target_location_logits = target_location_logits * target_location_mask.float().unsqueeze(-1)
        location_out = location_out * target_location_mask.long()
        location_out = location_out

        return target_location_logits, location_out


def test():
    batch_size = 2
    autoregressive_embedding = torch.randn(batch_size, AHP.autoregressive_embedding_size)
    action_type_sample = 65  # func: 65/Effect_PsiStorm_pt (1/queued [2]; 2/unit_tags [512]; 0/world [0, 0])
    action_type = torch.randint(low=0, high=SFS.available_actions, size=(batch_size, 1))

    map_skip = []
    if AHP == MAHP:
        for i in range(5):
            map_skip.append(torch.randn(batch_size, AHP.location_head_max_map_channels, 8, 8))
    else:
        for i in range(5):
            map_skip.append(torch.randn(batch_size, AHP.location_head_max_map_channels, 16, 16))

    location_head = LocationHead()

    print("autoregressive_embedding:", autoregressive_embedding) if debug else None
    print("autoregressive_embedding.shape:", autoregressive_embedding.shape) if 1 else None

    target_location_logits, target_location = \
        location_head.forward(autoregressive_embedding, action_type, map_skip)

    if target_location_logits is not None:
        print("target_location_logits:", target_location_logits) if debug else None
        print("target_location_logits.shape:", target_location_logits.shape) if debug else None
    else:
        print("target_location_logits is None!")

    if target_location is not None:
        print("target_location:", target_location) if debug else None
        # print("target_location.shape:", target_location.shape) if debug else None
    else:
        print("target_location is None!")

    print("This is a test!") if debug else None


if __name__ == '__main__':
    test()
