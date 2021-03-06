# -*- coding: utf-8 -*-

"""
Description: Train the DPL Model
Author: wondervictor
"""

import os
import torch
import random
import argparse
import numpy as np
import torch.cuda
import torch.nn as nn
import torch.utils.data
import torch.optim as optim
from torch.autograd import Variable

import utils
import models.dpl as model
import models.layers as layers
from datasets import pascal_voc
from datasets import utils as data_utils

parser = argparse.ArgumentParser()
parser.add_argument('--imageset', type=str, default='train', help='training image set [train, trainval]')
parser.add_argument('--batch_size', type=int, default=2, help='training batch size')
parser.add_argument('--basemodel', type=str, default='vgg', help='base cnn model:[vgg, resnet34, resnet50]')
parser.add_argument('--cuda', action='store_true', help='use GPU to train')
parser.add_argument('--dataset', type=str, default='VOC2012', help='training dataset:[VOC2012, VOC2007, COCO]')
parser.add_argument('--epoch', type=int, default=100, help='training epoches')
parser.add_argument('--lr', type=float, default=0.001, help='base learning rate')
parser.add_argument('--data_dir', type=str, required=True, help='parameters storage')
parser.add_argument('--log_interval', type=int, default=20, help='log messages interval')
parser.add_argument('--val_interval', type=int, default=5, help='validation interval')
parser.add_argument('--save_interval', type=int, default=5, help='save model interval')
parser.add_argument('--name', type=str, required=True, help='expriment name')
parser.add_argument('--img_size', type=int, default=224, help='image size')
parser.add_argument('--num_class', type=int, default=20, help='label classes')
parser.add_argument('--proposal', type=str, default='selective_search', help='proposal:[selective_search, dense_box]')
parser.add_argument('--resume', action='store_true', help='use saved parameters to resume')
parser.add_argument('--optimize_all', action='store_true', help='Optimize all parameters including VGG/ResNet')
parser.add_argument('--param', type=str, default='', help='Initialize with params')
opt = parser.parse_args()
print(opt)

# fix the random seed
random_seed = np.random.randint(0, 1000000)
random.seed(random_seed)
np.random.seed(random_seed)
torch.manual_seed(random_seed)

if not os.path.exists('output/'):
    os.mkdir('output/')

expr_dir = 'output/{}/'.format(opt.name)

if not os.path.exists(expr_dir):
    os.mkdir(expr_dir)

if torch.cuda.is_available() and not opt.cuda:
    print("WARNING: You have a CUDA device, so you should probably run with --cuda")

train_dataset = pascal_voc.PASCALVOC(
    data_dir=opt.data_dir,
    imageset=opt.imageset,
    roi_path='./data/',
    roi_type=opt.proposal,
    devkit='./devkit/'
)

val_dataset = pascal_voc.PASCALVOC(
    data_dir=opt.data_dir,
    imageset='val',
    roi_path='./data/',
    roi_type=opt.proposal,
    devkit='./devkit/'
)

train_loader = torch.utils.data.DataLoader(
    dataset=train_dataset,
    batch_size=opt.batch_size,
    shuffle=True,
    collate_fn=data_utils.collate_fn,
    num_workers=2
)

test_loader = torch.utils.data.DataLoader(
    dataset=val_dataset,
    batch_size=1,
    shuffle=False,
    collate_fn=data_utils.collate_fn,
    num_workers=2
)


def adjust_lr(_optimizer, _epoch):
    lr = opt.lr * 0.5 * (_epoch/5)
    for param_group in _optimizer.param_groups:
        lr = param_group['lr']
        param_group['lr'] = lr * 0.5


log_dir = expr_dir+'log/'
if not os.path.exists(log_dir):
    os.mkdir(log_dir)


param_dir = expr_dir+'param/'
if not os.path.exists(param_dir):
    os.mkdir(param_dir)

criterion = layers.MultiSigmoidCrossEntropyLoss()

if opt.optimize_all:
    dpl = model.DPL(use_cuda=opt.cuda, enable_base_grad=True, base=opt.basemodel, num_classes=opt.num_class)
    net_params = dpl.parameters()
else:
    dpl = model.DPL(use_cuda=opt.cuda, enable_base_grad=False, base=opt.basemodel, num_classes=opt.num_class)
    net_params = dpl.head_network.parameters()

optimizer = optim.Adam(params=net_params, lr=opt.lr, weight_decay=1e-4)

dpl.train()
# dpl = nn.DataParallel(dpl, device_ids=(0, 1))
logger = utils.Logger(stdio=True, log_file=log_dir+"training.log")
images = Variable(torch.FloatTensor(opt.batch_size, 3, opt.img_size, opt.img_size))
labels = Variable(torch.FloatTensor(opt.batch_size, opt.num_class))
if opt.cuda:
    criterion = criterion.cuda()
    dpl = dpl.cuda()
    images = images.cuda()
    labels = labels.cuda()

if opt.resume and os.path.exists("{}resume.pth".format(param_dir)):
    dpl.load_state_dict(torch.load("{}resume.pth".format(param_dir)))
    print("Load params from resume data")
elif len(opt.param) > 0:
    dpl.load_state_dict(torch.load(opt.param))
    print("Load params from param: %s" % opt.param)
print(dpl)
print("---------- DPL Model Init Finished -----------")

averager = utils.Averager()


def load_data(v, data):
    v.data.resize_(data.size()).copy_(data)


def test(net, criterion, output_dir):
    # output_dir = 'devkit/results/VOC2012/Main/comp2_cls_val_xxxx.txt'
    net.eval()
    test_iter = iter(test_loader)
    test_averager = utils.Averager()
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    i = 0
    while i < len(train_loader):
        img, lbl, box = train_iter.next()
        load_data(images, img)
        load_data(labels, lbl)
        boxes = Variable(torch.FloatTensor(box)).cuda()
        output = net(images, boxes).squeeze(0)
        loss = criterion(output, labels)
        test_averager.add(loss)

        for m in xrange(opt.num_class):
            cls_file = os.path.join(output_dir, 'cls_val_' + val_dataset.classes[m] + '.txt')
            with open(cls_file, 'a') as f:
                f.write(val_dataset.image_index[i] + ' ' + str(output[m]) + '\n')

            print 'im_cls: {:d}/{:d}: {}'.format(i + 1, len(train_loader), val_dataset.image_index[i])

    val_dataset.do_python_eval(output_dir)


def train_batch(net, data, criterion, optimizer):

    img, lbl, box, shapes = data
    load_data(images, img)
    load_data(labels, lbl)
    boxes = Variable(torch.FloatTensor(box)).cuda()
    shapes = Variable(torch.FloatTensor(shapes)).cuda()
    cls_1, cls_2, _ = net(images, shapes, boxes)

    loss1 = criterion(cls_1, labels)
    loss2 = criterion(cls_2, labels)
    loss = loss1 + loss2
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()

    return loss


logger.log('starting to train')
iter_steps = 0
for epoch in xrange(opt.epoch):
    train_iter = iter(train_loader)
    i = 0
    while i < len(train_loader):

        dpl.train()
        dpl.freeze_bn()
        data = train_iter.next()
        _loss = train_batch(dpl, data, criterion, optimizer=optimizer)
        averager.add(_loss)
        iter_steps += 1
        i += 1
        if (iter_steps+1) % opt.log_interval == 0:
            logger.log('[%d/%d][%d/%d] Loss: %f' % (epoch, opt.epoch, i, len(train_loader), averager.val()))

    averager.reset()
    if (epoch+1) % opt.val_interval == 0:
        pass
    # if (epoch+1) % opt.save_interval == 0:
    torch.save(dpl.state_dict(), os.path.join(param_dir, 'epoch_{}.pth'.format(epoch)))
    torch.save(dpl.state_dict(), os.path.join(param_dir, 'resume.pth'))


