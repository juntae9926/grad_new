# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import argparse
import enum
from torch import nn
from torch.nn import functional as F
from torchvision.models.resnet import resnet18, resnet50, resnext101_32x8d
from torchvision.models.efficientnet import efficientnet_b0
from torchvision.models.mobilenetv3 import mobilenet_v3_large
from torchvision.models.regnet import regnet_x_800mf, regnet_y_800mf
from classy_vision.models import build_model
from .gem_pooling import GlobalGeMPool2d
from pytorch_lightning import LightningModule


class Implementation(enum.Enum):
    CLASSY_VISION = enum.auto()
    TORCHVISION = enum.auto()


class Backbone(enum.Enum):
    CV_RESNET18 = ("resnet18", 512, Implementation.CLASSY_VISION)
    CV_RESNET50 = ("resnet50", 2048, Implementation.CLASSY_VISION)
    CV_RESNEXT101 = ("resnext101_32x4d", 2048, Implementation.CLASSY_VISION)


    TV_EFFICIENTNET_B0 = (efficientnet_b0, 1280, Implementation.TORCHVISION)
    TV_MOBILENETV3 = (mobilenet_v3_large, 1280, Implementation.TORCHVISION) # 
    TV_REGNET_X_800MF = (regnet_x_800mf, 672, Implementation.TORCHVISION)
    TV_REGNET_Y_800MF = (regnet_y_800mf, 784, Implementation.TORCHVISION) # 6,432,512 sum(p.numel() for p in models.regnet.regnet_y_800mf().parameters())

    TV_RESNET18 = (resnet18, 512, Implementation.TORCHVISION) # 11,689,512
    TV_RESNET50 = (resnet50, 2048, Implementation.TORCHVISION) # 25,557,032
    TV_RESNEXT101 = (resnext101_32x8d, 2048, Implementation.TORCHVISION)

    def build(self, dims: int):
        impl = self.value[2]
        if impl == Implementation.CLASSY_VISION:
            model = build_model({"name": self.value[0]})
            # Remove head exec wrapper, which we don't need, and breaks pickling
            # (needed for spawn dataloaders).
            return model.classy_model
        if impl == Implementation.TORCHVISION:
            return self.value[0](num_classes=dims, zero_init_residual=True)
        raise AssertionError("Model implementation not handled: %s" % (self.name,))


class L2Norm(nn.Module):
    def forward(self, x):
        return F.normalize(x)


class Model(LightningModule):
    def __init__(self, backbone: str, dims: int, pool_param: float):
        super().__init__()
        self.backbone_type = Backbone[backbone]
        self.backbone = self.backbone_type.build(dims=dims)
        impl = self.backbone_type.value[2]
        if impl == Implementation.CLASSY_VISION:
            self.embeddings = nn.Sequential(
                GlobalGeMPool2d(pool_param),
                nn.Linear(self.backbone_type.value[1], dims),
                L2Norm(),
            )
        elif impl == Implementation.TORCHVISION:
            if pool_param > 1:
                self.backbone.avgpool = GlobalGeMPool2d(pool_param)
                if 'RESNET' in backbone:
                    fc = self.backbone.fc
                    nn.init.xavier_uniform_(fc.weight)
                    nn.init.constant_(fc.bias, 0)
                elif 'EFFICIENT' in backbone:
                    fc = self.backbone.classifier[1]
                    nn.init.xavier_uniform_(fc.weight)
                    nn.init.constant_(fc.bias, 0)
            self.embeddings = L2Norm()

    def forward(self, x):
        x = self.backbone(x)
        return self.embeddings(x)

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser):
        parser = parser.add_argument_group("Model")
        parser.add_argument(
            "--backbone", default="TV_RESNET18", choices=[b.name for b in Backbone]
        )
        parser.add_argument("--dims", default=512, type=int)
        parser.add_argument("--pool_param", default=3, type=float)
