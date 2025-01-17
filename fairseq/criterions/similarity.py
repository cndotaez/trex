# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import math

import torch
import torch.nn.functional as F

from fairseq import metrics, utils
from fairseq.criterions import FairseqCriterion, register_criterion
from sklearn.metrics import roc_curve, roc_auc_score
import itertools
from command import configs


@register_criterion('similarity')
class SimilarityCriterion(FairseqCriterion):
    def __init__(self, task, classification_head_name):
        super().__init__(task)
        self.classification_head_name = classification_head_name
        self.fields = configs.fields

    @staticmethod
    def add_args(parser):
        # fmt: off
        parser.add_argument('--classification-head-name',
                            default='similarity',
                            help='name of the classification head to use')
        # fmt: on

    def forward(self, model, sample, reduce=True):
        """Compute the loss for the given sample.

        Returns a tuple with three elements:
        1) the loss
        2) the sample size, which is used as the denominator for the gradient
        3) logging outputs to display while training
        """
        assert (
                hasattr(model, 'classification_heads')
                and self.classification_head_name in model.classification_heads
        ), 'model must provide sentence classification head for --criterion=similarity'

        logits0, _ = model(
            **sample['net_input0'],
            classification_head_name=self.classification_head_name
        )
        logits1, _ = model(
            **sample['net_input1'],
            classification_head_name=self.classification_head_name
        )
        targets = model.get_targets(sample, [logits0]).view(-1)
        sample_size = targets.numel()

        loss = F.cosine_embedding_loss(logits0, logits1, targets, margin=configs.cosine_embedding_loss_margin,
                                       reduction='sum')

        # targets = targets.float()
        # loss = F.mse_loss(
        #     torch.cosine_similarity(logits0, logits1, dim=1),
        #     targets,
        #     reduction='sum',
        # )

        logging_output = {
            'loss': loss.data,
            'ntokens': sample['ntokens0'] + sample['ntokens1'],
            'nsentences': sample_size,
            'sample_size': sample_size,
        }

        preds = torch.cosine_similarity(logits0, logits1, dim=1)
        logging_output['ncorrect'] = (((preds > configs.cosine_embedding_loss_margin) == (
                targets > configs.cosine_embedding_loss_margin)) * (
                                              targets > configs.cosine_embedding_loss_margin)).sum().item()
        logging_output['ncorrect_total'] = ((preds > configs.cosine_embedding_loss_margin) == (
                targets > configs.cosine_embedding_loss_margin)).sum().item()
        logging_output['ncorrect_pred'] = (preds > configs.cosine_embedding_loss_margin).sum().item()
        logging_output['ncorrect_actual'] = (targets > configs.cosine_embedding_loss_margin).sum().item()

        logging_output['preds'] = preds.detach().cpu().numpy().tolist()
        logging_output['targets'] = targets.detach().cpu().numpy().tolist()

        # try:
        #     targets_np = targets.detach().cpu().numpy().tolist()
        #     preds_np = preds.detach().cpu().numpy().tolist()
        #     logging_output['auc'] = roc_auc_score(targets_np, preds_np)
        #     # if logging_output['auc'] > 0.95:
        #     #     self.preds.write(' '.join([str(i) for i in preds_np.tolist()]) + '\n')
        #     #     self.targets.write(' '.join([str(i) for i in targets_np.tolist()]) + '\n')
        #
        # except ValueError:
        #     logging_output['auc'] = 0.

        return loss, sample_size, logging_output

    @staticmethod
    def reduce_metrics(logging_outputs) -> None:
        """Aggregate logging outputs from data parallel training."""
        loss_sum = sum(log.get('loss', 0) for log in logging_outputs)
        ntokens = sum(log.get('ntokens', 0) for log in logging_outputs)
        nsentences = sum(log.get('nsentences', 0) for log in logging_outputs)
        sample_size = sum(log.get('sample_size', 0) for log in logging_outputs)

        metrics.log_scalar('loss', loss_sum / sample_size / math.log(2), sample_size, round=3)
        if sample_size != ntokens:
            metrics.log_scalar('nll_loss', loss_sum / ntokens / math.log(2), ntokens, round=3)

        if len(logging_outputs) > 0 and 'ncorrect' in logging_outputs[0] \
                and 'ncorrect_pred' in logging_outputs[0] and 'ncorrect_actual' in logging_outputs[0] \
                and 'ncorrect_total' in logging_outputs[0] \
                and 'preds' in logging_outputs[0] and 'targets' in logging_outputs[0]:

            preds = list(itertools.chain.from_iterable([log.get('preds', 0) for log in logging_outputs]))
            targets = list(itertools.chain.from_iterable([log.get('targets', 0) for log in logging_outputs]))

            if len(set(targets)) != 2:
                auc = 1.
            else:
                auc = roc_auc_score(targets, preds)

            ''' F1 is messy as it depends on chosen threshold, don't use..
            ncorrect = sum(log.get('ncorrect', 0) for log in logging_outputs)
            ncorrect_pred = sum(log.get('ncorrect_pred', 0) for log in logging_outputs)
            ncorrect_actual = sum(log.get('ncorrect_actual', 0) for log in logging_outputs)
            ncorrect_total = sum(log.get('ncorrect_total', 0) for log in logging_outputs)
            
            precision = 100 * ncorrect / (ncorrect_pred + 1e-5)
            recall = 100 * ncorrect / (ncorrect_actual + 1e-5)

            metrics.log_scalar('accuracy', 100.0 * ncorrect_total / nsentences, nsentences, round=1)
            metrics.log_scalar('precision', precision, nsentences, round=1)
            metrics.log_scalar('recall', recall, nsentences, round=1)
            metrics.log_scalar('F1', 2 * (precision * recall) / (precision + recall + 1e-5), nsentences, round=1)
            '''
            metrics.log_scalar('AUC', auc, nsentences, round=4)

    @staticmethod
    def logging_outputs_can_be_summed() -> bool:
        """
        Whether the logging outputs returned by `forward` can be summed
        across workers prior to calling `reduce_metrics`. Setting this
        to True will improves distributed training speed.
        """
        return False
