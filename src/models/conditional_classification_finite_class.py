import torch
import torch.nn as nn
from torch.utils.data import random_split
from tqdm import tqdm
from typing import List, Tuple
from ..utils.helpers import Classify, TransformedDataset
from ..models.projected_stochastic_gradient_descent import SelectorPerceptron

class ConditionalLearnerForFiniteClass(nn.Module):
    def __init__(
            self, 
            prev_header: str,
            dim_sample: int,
            num_iter: int, 
            sample_size_psgd: int,
            lr_coeff: float = 0.5,
            batch_size: int = 32
    ):
        """
        Initialize the conditional learner for finite class classification.

        Parameters:
        dim_sample (int):             The dimension of the sample features.
        num_iter (int):               The number of iterations for SGD.
        lr_coeff (float):             The learning rate coefficient.
        sample_size_psgd (float):          The ratio of training samples.
        batch_size (int):             The batch size for SGD.
        """
        super(ConditionalLearnerForFiniteClass, self).__init__()
        self.header = " ".join([prev_header, "conditional learner", "-"])
        self.dim_sample = dim_sample
        self.num_iter = num_iter
        self.batch_size = batch_size
        self.sample_size_psgd = sample_size_psgd

        self.lr_beta = lr_coeff * torch.sqrt(
            torch.tensor(
                1 / (num_iter * dim_sample)
            )
        )
        self.init_weight = torch.zeros(self.dim_sample, dtype=torch.float32)
        self.init_weight[0] = 1

    def forward(
            self, 
            data: torch.Tensor,
            sparse_classifier_clusters: List[torch.sparse.FloatTensor]
    ) -> torch.Tensor:
        """
        Perform conditional learning for finite class classification.


        Parameters:
        sparse_classifier_clusters (List[torch.sparse.FloatTensor]): The list of sparse classifiers.

        Returns:
        selector_list (torch.Tensor): The list of weights for each classifier.
                                      The weight_list is represented as a sparse tensor.
                                      The order of the weight vectors in the list is the same as the following two loops:
                                      for features in feature_combinations:
                                          for samples in sample_combinations:
                                              ...
        """        
        
        num_cluster = len(sparse_classifier_clusters)
        candidate_selectors = torch.zeros(
            [num_cluster, self.dim_sample]
        ).to(data.device)
        candidate_classifiers = torch.zeros(
            [num_cluster, self.dim_sample]
        ).to(data.device)

        # initialize evaluation dataset for conditional learner
        eval_dataset = TransformedDataset(data)

        for i, classifiers in enumerate(
            tqdm(
                sparse_classifier_clusters, 
                total=num_cluster, 
                desc=self.header
            )
        ):
            dataset = TransformedDataset(data, classifiers)
            dataset_train, dataset_val = random_split(
                dataset, 
                [self.sample_size_psgd, len(dataset) - self.sample_size_psgd]
            )
            selector_learner = SelectorPerceptron(
                prev_header=self.header + ">",
                dim_sample=self.dim_sample,
                cluster_id = i + 1,
                cluster_size=classifiers.size(0),
                num_iter=self.num_iter,
                lr_beta=self.lr_beta,
                batch_size=self.batch_size,
                device=data.device
            )
            selectors = selector_learner(
                dataset_train=dataset_train,
                dataset_val=dataset_val,
                init_weight=self.init_weight.to(data.device)
            )  # [cluster size, dim sample]

            candidate_classifiers[i, ...], candidate_selectors[i, ...] = self.evaluate(
                eval_dataset=eval_dataset,   # could use different data for each evaluation
                classifiers=classifiers.to_dense(),
                selectors=selectors
            )

        print(f"{self.header} evaluating for the final candidates...")
        return self.evaluate(
            eval_dataset=eval_dataset,
            classifiers=candidate_classifiers,
            selectors=candidate_selectors
        )

    def evaluate(
            self,
            eval_dataset: TransformedDataset,
            classifiers: torch.Tensor,
            selectors: torch.Tensor
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        labels, features = eval_dataset[:]

        errors = (
            Classify(
                classifier=classifiers,
                data=features.T
            )
        ) != labels
        selections = Classify(
            classifier=selectors,
            data=features.T
        )

        conditional_error_rate = (errors * selections).sum(dim=-1) / selections.sum(dim=-1)
        # replace NaN to 1
        conditional_error_rate[torch.isnan(conditional_error_rate)] = 1

        min_error, min_index = torch.min(conditional_error_rate, dim=0)

        return classifiers[min_index], selectors[min_index]