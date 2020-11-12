import os
import gc
import sys
import argparse
from itertools import product
from time import time
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
from scipy.io import savemat, loadmat
from utils import elapsed_time
from params import TIME_TRIAL_LENGTH
from dataloaders import create_loaders

torchsum = True
try:
    from torchsummary import summary
except:
    print("Error loading torchsummary")
    torchsum = False

parser = argparse.ArgumentParser()
parser.add_argument(
    "--save",
    type=str,
    help="The path where the model will be saved.",
)
parser.add_argument(
    "-p",
    "--path",
    type=str,
    help="The path where the data samples can be found.",
)
parser.add_argument(
    "--seed",
    default=420,
    type=int,
    help="Seed to use for random splits.",
)
parser.add_argument(
    "-s",
    "--max-subj",
    default=1000,
    type=int,
    help="maximum number of subjects to use (1000 uses all subjects)",
)
parser.add_argument(
    "-e",
    "--elec",
    default="MAG",
    choices=["GRAD", "MAG", "ALL"],
    help="The type of electrodes to keep, default=MAG",
)
parser.add_argument(
    "--feature",
    default="temporal",
    choices=["temporal", "bands", "bins"],
    help="Data type to use.",
)
parser.add_argument(
    "-b",
    "--batch-size",
    default=128,
    type=int,
    help="The batch size used for learning.",
)
parser.add_argument(
    "-d",
    "--dropout",
    default=0.25,
    type=float,
    help="The dropout rate of the linear layers",
)
parser.add_argument(
    "--debug",
    action="store_true",
    help="loads dummy data in the net to ensure everything is working fine",
)
parser.add_argument(
    "--dropout_option",
    default="same",
    choices=["same", "double", "inverted"],
    help="sets if the first dropout and the second are the same or if the first one or the second one should be bigger",
)
parser.add_argument(
    "-l", "--linear", type=int, help="The size of the second linear layer"
)
parser.add_argument(
    "-m",
    "--mode",
    type=str,
    choices=["overwrite", "continue", "empty_run"],
    default="continue",
    help="CHANGE THIS TODO",
)
parser.add_argument(
    "-f", "--filters", type=int, help="The size of the first convolution"
)
parser.add_argument(
    "-n",
    "--nchan",
    type=int,
    help="the number of channels for the first convolution, the other channel numbers scale with this one",
)


def accuracy(y_pred, target):
    # Compute accuracy from 2 vectors of labels.
    correct = torch.eq(y_pred.max(1)[1], target).sum().type(torch.FloatTensor)
    return correct / len(target)


class Flatten(nn.Module):
    # Flatten layer used to connect between feature extraction and classif parts of a net.
    def forward(self, x):
        x = x.view(x.size(0), -1)
        return x


def load_checkpoint(filename):
    # Function to load a network state from a filename.
    print("=> loading checkpoint '{}'".format(filename))
    checkpoint = torch.load(filename)
    start_epoch = checkpoint["epoch"]
    model_state = checkpoint["state_dict"]
    optimizer_state = checkpoint["optimizer"]
    return start_epoch, model_state, optimizer_state


def save_checkpoint(state, filename="checkpoint.pth.tar"):
    # Saves a checkpoint of the network
    torch.save(state, filename)


def train(
    net,
    trainloader,
    validloader,
    model_filepath,
    criterion=nn.CrossEntropyLoss(),
    optimizer=optim.Adam,
    save_model=False,
    load_model=False,
    debug=False,
    p=20,
    lr=0.0001,
):
    # The train function trains and evaluates the network multiple times and prints the
    # loss and accuracy for each batch and each epoch. Everything is saved in a dictionnary
    # with the best checkpoint of the network.

    if debug:
        optimizer = optimizer(net.parameters())
    else:
        optimizer = optimizer(net.parameters(), lr=lr)

    # Load if asked and if the checkpoint exists in the specified path
    if load_model and os.path.exists(model_filepath):
        epoch, net_state, optimizer_state = load_checkpoint(model_filepath)
        net.load_state_dict(net_state)
        optimizer.load_state_dict(optimizer_state)
        results = loadmat(model_filepath[:-2] + "mat")
        best_vacc = results["acc_score"]
        best_vloss = results["loss_score"]
        valid_accs = results["acc"]
        train_accs = results["train_acc"]
        valid_losses = results["valid_loss"]
        train_losses = results["train_loss"]
        best_epoch = results["best_epoch"]
        epoch = results["n_epochs"]
    elif load_model:
        print(f"Couldn't find any checkpoint named {net.name} in {save_path}")
    else:
        epoch = 0

    j = 0
    train_accs = []
    valid_accs = []
    train_losses = []
    valid_losses = []
    best_vloss = float("inf")
    net.train()

    # The training and evaluation loop with patience early stop. j tracks the patience state.
    while j < p:
        epoch += 1
        N_BATCHES = len(trainloader)
        for i, batch in enumerate(trainloader):
            optimizer.zero_grad()
            X, y = batch

            y = y.view(-1).to(device)
            X = X.view(-1, *net.input_size).float().to(device)

            net.train()
            out = net.forward(X)
            loss = criterion(out, y)
            loss.backward()
            optimizer.step()
            print(
                f"Epoch: {epoch} // Batch {i+1}/{N_BATCHES} // loss = {loss}", end="\r"
            )

        train_loss, train_acc = evaluate(net, trainloader, criterion)
        valid_loss, valid_acc = evaluate(net, validloader, criterion)

        train_accs.append(train_acc)
        train_losses.append(train_loss)
        valid_losses.append(valid_loss)
        valid_accs.append(valid_acc)
        if valid_loss < best_vloss:
            best_vacc = valid_acc
            best_vloss = valid_loss
            best_net = net
            best_epoch = epoch
            j = 0
            if save_model:
                checkpoint = {
                    "epoch": epoch + 1,
                    "state_dict": best_net.state_dict(),
                    "optimizer": optimizer.state_dict(),
                }
                save_checkpoint(checkpoint, model_filepath)
                net.save_model(save_path)
        else:
            j += 1

        print("Epoch: {}".format(epoch))
        print(" [LOSS] TRAIN {} / VALID {}".format(train_loss, valid_loss))
        print(" [ACC] TRAIN {} / VALID {}".format(train_acc, valid_acc))
        if save_model:
            results = {
                "acc_score": [best_vacc],
                "loss_score": [best_vloss],
                "acc": valid_accs,
                "train_acc": train_accs,
                "valid_loss": valid_losses,
                "train_loss": train_losses,
                "best_epoch": best_epoch,
                "n_epochs": epoch,
            }
            savemat(save_path + net.name + ".mat", results)

    return net


def evaluate(net, dataloader, criterion=nn.CrossEntropyLoss()):
    # function to evaluate a network on a dataloader. will return loss and accuracy
    net.eval()
    with torch.no_grad():
        LOSSES = 0
        ACCURACY = 0
        COUNTER = 0
        for batch in dataloader:
            X, y = batch
            y = y.view(-1).to(device)
            X = X.view(-1, *net.input_size).float().to(device)

            out = net.forward(X)
            loss = criterion(out, y)
            acc = accuracy(out, y)
            n = y.size(0)
            LOSSES += loss.sum().data.cpu().numpy() * n
            ACCURACY += acc.sum().data.cpu().numpy() * n
            COUNTER += n
        floss = LOSSES / float(COUNTER)
        faccuracy = ACCURACY / float(COUNTER)
    return floss, faccuracy


class customNet(nn.Module):
    def __init__(self, model_name, input_size):
        super(customNet, self).__init__()
        self.input_size = input_size
        self.name = model_name
        print(model_name)

    def _get_lin_size(self, layers):
        return nn.Sequential(*layers)(torch.zeros((1, *input_size))).shape[-1]

    def forward(self, x):
        return self.model(x)

    def save_model(self, filepath="."):
        if not filepath.endswith("/"):
            filepath += "/"

        orig_stdout = sys.stdout
        with open(filepath + self.name + ".txt", "a") as f:
            sys.stdout = f
            if torchsum:
                summary(self, (input_size))
            else:
                print(self)
            sys.stdout = orig_stdout


class FullNet(customNet):
    def __init__(
        self,
        model_name,
        input_size,
        filter_size=50,
        n_channels=5,
        n_linear=150,
        dropout=0.3,
        dropout_option="same",
    ):
        super(FullNet, self).__init__(model_name, input_size)
        if dropout_option == "same":
            dropout1 = dropout
            dropout2 = dropout
        else:
            assert (
                dropout < 0.5
            ), "dropout cannot be higher than .5 in this configuration"
            if dropout_option == "double":
                dropout1 = dropout
                dropout2 = dropout * 2
            elif dropout_option == "inverted":
                dropout1 = dropout * 2
                dropout2 = dropout
            else:
                print(f"{dropout_option} is not a valid option")

        layers = nn.ModuleList(
            [
                nn.Conv2d(1, 5 * n_channels, (1, filter_size)),
                nn.BatchNorm2d(5 * n_channels),
                nn.ReLU(),
                nn.Conv2d(5 * n_channels, 5 * n_channels, (input_size[1], 1)),
                nn.BatchNorm2d(5 * n_channels),
                nn.ReLU(),
                nn.MaxPool2d((1, 5)),
                nn.Conv2d(5 * n_channels, 8 * n_channels, (1, int(filter_size / 10))),
                nn.BatchNorm2d(8 * n_channels),
                nn.ReLU(),
                nn.MaxPool2d((1, 5)),
                nn.Conv2d(8 * n_channels, 16 * n_channels, (1, int(filter_size / 5))),
                nn.BatchNorm2d(16 * n_channels),
                nn.ReLU(),
                Flatten(),
            ]
        )

        lin_size = self._get_lin_size(layers)
        layers.extend(
            (
                nn.Dropout(dropout1),
                nn.Linear(lin_size, n_linear),
                nn.Dropout(dropout2),
                nn.Linear(n_linear, 2),
            )
        )

        self.model = nn.Sequential(*layers)


class vanPutNet(customNet):
    def __init__(self, model_name, input_size, dropout=0.25):

        super(vanPutNet, self).__init__(model_name, input_size)
        layers = nn.ModuleList(
            [
                nn.Conv2d(1, 100, 3),
                nn.ReLU(),
                nn.MaxPool2d((2, 2)),
                nn.Dropout(dropout),
                nn.Conv2d(100, 100, 3),
                nn.MaxPool2d((2, 2)),
                nn.Dropout(dropout),
                nn.Conv2d(100, 300, (2, 3)),
                nn.MaxPool2d((2, 2)),
                nn.Dropout(dropout),
                nn.Conv2d(300, 300, (1, 7)),
                nn.MaxPool2d((2, 2)),
                nn.Dropout(dropout),
                nn.Conv2d(300, 100, (1, 3)),
                nn.Conv2d(100, 100, (1, 3)),
                Flatten(),
            ]
        )

        lin_size = self._get_lin_size(layers)
        layers.append(nn.Linear(lin_size, 2))
        self.model = nn.Sequential(*layers)


if __name__ == "__main__":

    if torch.cuda.is_available():
        device = "cuda"
    else:
        device = "cpu"

    ###############
    ### PARSING ###
    ###############

    args = parser.parse_args()
    data_path = args.path
    save_path = args.save
    if not save_path.endswith("/"):
        save_path += "/"
    data_type = args.feature
    batch_size = args.batch_size
    max_subj = args.max_subj
    ch_type = args.elec
    features = args.feature
    debug = args.debug
    filters = args.filters
    nchan = args.nchan
    dropout = args.dropout
    dropout_option = args.dropout_option
    linear = args.linear
    seed = args.seed
    mode = args.mode

    ##################
    ### data types ###
    ##################

    if ch_type == "MAG":
        n_channels = 102
    elif ch_type == "GRAD":
        n_channels = 204
    elif ch_type == "all":
        n_channels = 306

    if features == "bins":
        bands = False
        trial_length = 241
    if features == "bands":
        bands = False
        trial_length = 5
    elif features == "temporal":
        trial_length = TIME_TRIAL_LENGTH

    ###########################
    ### learning parameters ###
    ###########################

    patience = 20
    learning_rate = 0.00001
    train_size = 0.8
    if debug:
        print("ENTERING DEBUG MODE")
        nchan = 102
        dropout = 0
        dropout_option = "same"

    #########################
    ### preparing network ###
    #########################

    # net = FullNet("", filters, nchan)
    # lin_size = compute_lin_size(np.zeros((2, 1, N_CHANNELS, TRIAL_LENGTH)), net)

    # net = FullNet(
    #     f"model_{dropout_option}_dropout{dropout}_filter{filters}_nchan{nchan}_lin{linear}",
    #     filters,
    #     nchan,
    #     linear,
    #     dropout,
    #     dropout_option,
    #     lin_size,
    # )
    # lin_size = compute_lin_size(np.zeros((2, 1, N_CHANNELS, TRIAL_LENGTH)), net)

    input_size = (1, n_channels, trial_length)
    net = vanPutNet(
        model_name="van_Putten_network", input_size=input_size, dropout=dropout
    ).to(device)

    if torchsum:
        print(summary(net, input_size))
    else:
        print(net)

    # We create loaders and datasets (see dataloaders.py)
    trainloader, validloader, testloader = create_loaders(
        data_path,
        train_size,
        batch_size,
        max_subj,
        ch_type,
        data_type,
        seed=seed,
    )

    if mode == "overwrite":
        save = True
        load = False
    elif mode == "continue":
        save = True
        load = True
    else:
        save = False
        load = False

    model_filepath = save_path + net.name + ".pt"
    # Actual training (loading nework if existing and load option is True)
    train(
        net,
        trainloader,
        validloader,
        model_filepath,
        save_model=save,
        load_model=load,
        debug=debug,
        p=patience,
        lr=learning_rate,
    )

    # Loading best saved model
    _, net_state, _ = load_checkpoint(model_filepath)
    net.load_state_dict(net_state)

    # Final testing
    print("Evaluating on test set:")
    print(evaluate(net, testloader))
