# -*- coding: utf-8 -*-

"""
Description: Layers Definition
Author: wondervictor
"""

import sys
import torch
import numpy as np
import torch.nn as nn
from torch.autograd import Variable
sys.path.append('./lib')
from model.roi_align.modules import roi_align


class MultiSigmoidCrossEntropyLoss(nn.Module):
    """ MultiClass Sigmoid Cross Entropy Loss
    Inputs:
        - `s`: the input score
        - `y`: the target label

    Shape:
        - Input: :math:`(N, C)`
        - Output: :math:`(N, C)`

    """
    def __init__(self):
        super(MultiSigmoidCrossEntropyLoss, self).__init__()
        self.bce = nn.BCEWithLogitsLoss()

    def forward(self, s, y):
        # s: batch * class
        # y: batch * class
        class_num = y.size()[1]

        loss = self.bce(s, y) * class_num

        return loss

    def __repr__(self):
        return self.__class__.__name__


class ROIPooling1(nn.Module):
    """ ROI Pooling Version1
    Args:
        pool_size (int): ROI Pooling size
        scale (float): scale for input features which were downsampled by pooling layers in convolution layers
    Inputs:
        - `features`: the input features
        - `rois`: the target label

    Shape:
        - Input: :math:`(N, C)`
        - Output: :math:`(N, C)`

    """
    def __init__(self, pool_size, scale, cuda):
        super(ROIPooling1, self).__init__()
        self.pool_size = pool_size
        self.scale = scale
        self.cuda = cuda

    def forward(self, features, rois):
        # features  B*C*H*W
        # rois      B*N*4 (px, py, qx, qy)
        batch_size, num_ch, height, width = features.size()
        num_rois = rois.size()[1]
        output = Variable(torch.FloatTensor(batch_size, num_rois, num_ch, self.pool_size, self.pool_size))
        if self.cuda:
            output = output.cuda()
        for b in xrange(batch_size):
            for roindex in xrange(num_rois):
                px, py, qx, qy = np.round(rois[b, roindex].data.cpu().numpy() * self.scale).astype(int)
                feature_part = features[b, :, py:qy+1, px: qx+1]
                roi_width = feature_part.size()[2]
                roi_height = feature_part.size()[1]
                # pool kernel size
                psize_w = max(1,int(np.ceil(float(roi_width) / self.pool_size)))
                psize_h = max(1,int(np.ceil(float(roi_height) / self.pool_size)))
                pad_top = (psize_h * self.pool_size - roi_height)/2
                pad_left = (psize_w * self.pool_size - roi_width)/2
                pad_bottom = psize_h * self.pool_size - roi_height - pad_top
                pad_right = psize_w * self.pool_size - roi_width - pad_left
                maxpool = nn.MaxPool2d((psize_h, psize_w), stride=(psize_h, psize_w))
                # feature_part = features[b, :, py:qy+1, px: qx+1]
                pad = nn.ZeroPad2d(padding=(pad_left, pad_right, pad_top, pad_bottom))
                feature_part = pad(feature_part.unsqueeze(0)).squeeze(0)
                output[b, roindex] = maxpool(feature_part)

        return output

    def __repr__(self):
        return self.__class__.__name__ + ("Pool Size:{} Scale: {}".format(self.pool_size, self.scale))


class ROIPooling(nn.Module):

    """ ROI Pooling V2 for arbitray rois
    Args:
        pool_size (int): ROI Pooling size
        scale (float): scale for input features which were downsampled by pooling layers in convolution layers
    Inputs:
        - `features`: the input features
        - `rois`: the target label

    Shape:
        - Input: :math:`(N, C)`
        - Output: :math:`(N, C)`

    """
    def __init__(self, pool_size, scale, cuda):
        super(ROIPooling, self).__init__()
        self.pool_size = pool_size
        self.scale = scale
        self.cuda = cuda

    def forward(self, features, rois):
        # features: N*C*H*W
        # rois: N*5
        assert len(rois.size()) == 2 and rois.size()[1] == 5, "the shape of rois should be `Nx5`"
        batch_size, num_ch, height, width = features.size()
        num_rois = rois.size()[0]
        output = Variable(torch.FloatTensor(num_rois, num_ch, self.pool_size, self.pool_size))
        if self.cuda:
            output = output.cuda()
        output_batch_id = np.zeros(num_rois, dtype=np.int32)
        for roiidx, roi in enumerate(rois):
            batch_id = int(roi[0].data[0])
            px, py, qx, qy = np.round(roi.data[1:].cpu().numpy() * self.scale).astype(int)
            # roi_width = max(qx - px + 1, 1)
            # roi_height = max(qy - py + 1, 1)
            feature_part = features[batch_id, :, py:qy+1, px: qx+1]
            roi_width = feature_part.size()[2]
            roi_height = feature_part.size()[1]
            # pool kernel size
            psize_w = max(1,int(np.ceil(float(roi_width) / self.pool_size)))
            psize_h = max(1,int(np.ceil(float(roi_height) / self.pool_size)))            
            pad_top = (psize_h * self.pool_size - roi_height) / 2
            pad_left = (psize_w * self.pool_size - roi_width) / 2
            pad_bottom = psize_h * self.pool_size - roi_height - pad_top
            pad_right = psize_w * self.pool_size - roi_width - pad_left
            maxpool = nn.MaxPool2d((psize_h, psize_w), stride=(psize_h, psize_w))
            # feature_part = features[batch_id, :, py:qy + 1, px: qx + 1]
            pad = nn.ZeroPad2d(padding=(pad_left, pad_right, pad_top, pad_bottom))
            feature_part = pad(feature_part.unsqueeze(0)).squeeze(0)
            output[roiidx] = maxpool(feature_part)
            output_batch_id[roiidx] = batch_id

        return output, output_batch_id

    def __repr__(self):
        return self.__class__.__name__ + ("Pool Size:{} Scale: {}".format(self.pool_size, self.scale))


class PatchPooling(nn.Module):

    """ PatchPooling Layer
    Args:
        batch_size (int): batchsize of the patches
    Inputs:
        - `features`: the input features
        - `rois`: the target label

    Shape:
        - Input: :math:`(N, C)`
        - Output: :math:`(N, C)`

    """
    def __init__(self, cuda):
        super(PatchPooling, self).__init__()
        self.cuda = cuda

    def forward(self, batch_size, patches, patch_ids):
        # patches: torch.FloatTensor, NxC
        # patch_ids: numpy array, Nx1
        num_patch, num_features = patches.size()
        output = Variable(torch.FloatTensor(batch_size, num_features))
        if self.cuda:
            output = output.cuda()
        for i in xrange(batch_size):
            output[i] = torch.max(patches[np.where(patch_ids == i), :].squeeze(0), dim=0)[0]
        return output


class ROIAlign(nn.Module):

    def __init__(self, out_size, spatial_scale):
        super(ROIAlign, self).__init__()
        self.align = roi_align.RoIAlign(out_size, out_size, spatial_scale)

    def forward(self, features, rois):
        return self.align(features, rois)


class SPMMaxPooling(nn.Module):

    def __init__(self):
        super(SPMMaxPooling, self).__init__()

    def forward(self, x):
        pass
