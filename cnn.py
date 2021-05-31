import os
import gc
import sys
import logging
from copy import deepcopy
from itertools import product
from time import time
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
from torch.autograd import Variable
from scipy.io import savemat, loadmat
from utils import nice_time as nt
from params import TIME_TRIAL_LENGTH
from dataloaders import create_loaders
from parser import parser


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
    logging.info("=> loading checkpoint '{}'".format(filename))
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
    timing=False,
    mode="overwrite",
    p=20,
    lr=0.00001,
):
    # The train function trains and evaluates the network multiple times and prints the
    # loss and accuracy for each batch and each epoch. Everything is saved in a dictionnary
    # with the best checkpoint of the network.

    if debug:
        optimizer = optimizer(net.parameters())
    else:
        optimizer = optimizer(net.parameters(), lr=lr)

    # Load if asked and if the checkpoint exists in the specified path
    epoch = 0
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
        try:  # For backward compatibility purposes
            if mode == "continue":
                j = 0
                lpatience = patience
            else:
                j = results["current_patience"]
                lpatience = results["patience"]
        except:
            j = 0
            lpatience = patience

        if lpatience != patience:
            logging.warning(
                f"Warning: current patience ({patience}) is different from loaded patience ({lpatience})."
            )
            answer = input("Would you like to continue anyway ? (y/n)")
            while answer not in ["y", "n"]:
                answer = input("Would you like to continue anyway ? (y/n)")
            if answer == "n":
                exit()

    elif load_model:
        logging.warning(
            f"Warning: Couldn't find any checkpoint named {net.name} in {save_path}"
        )
        j = 0

    else:
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
        n_batches = len(trainloader)
        if timing:
            t1 = time()
        for i, batch in enumerate(trainloader):
            optimizer.zero_grad()
            X, y = batch

            y = y.view(-1).to(device)
            X = X.view(-1, *net.input_size).to(device)

            net.train()
            out = net.forward(X)
            loss = criterion(out, Variable(y.long()))
            loss.backward()
            optimizer.step()

            progress = f"Epoch: {epoch} // Batch {i+1}/{n_batches} // loss = {loss:.5f}"

            if timing:
                tpb = (time() - t1) / (i + 1)
                et = tpb * n_batches
                progress += f"// time per batch = {tpb:.5f} // epoch time = {nt(et)}"

            if n_batches > 10:
                if i % (n_batches // 10) == 0:
                    logging.info(progress)
            else:
                logging.info(progress)

            condition = i >= 999 or i == n_batches - 1
            if timing and condition:
                return tpb, et

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

        logging.info("Epoch: {}".format(epoch))
        logging.info(" [LOSS] TRAIN {} / VALID {}".format(train_loss, valid_loss))
        logging.info(" [ACC] TRAIN {} / VALID {}".format(train_acc, valid_acc))
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
                "patience": patience,
                "current_patience": j,
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
            X = X.view(-1, *net.input_size).to(device)

            out = net.forward(X)
            loss = criterion(out, Variable(y.long()))
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
        logging.info(model_name)

    def _get_lin_size(self, layers):
        return nn.Sequential(*layers)(torch.zeros((1, *self.input_size))).shape[-1]

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
                logging.info(self)
            sys.stdout = orig_stdout


class FullNet(customNet):
    def __init__(
        self,
        model_name,
        input_size,
        filter_size=7,
        nchan=5,
        n_linear=150,
        dropout=0.25,
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
                logging.warning("{} is not a valid option".format(dropout_option))

        layers = nn.ModuleList(
            [
                # equivalent to doing nn.Linear(input_size[0], nchan)
                nn.Conv2d(input_size[0], nchan, (input_size[1], 1)),
                nn.ReLU(),
                # Explore different stride and maybe dilation parameters:
                nn.Conv2d(nchan, nchan, (1, filter_size)),
                Flatten(),
                nn.Dropout(dropout),
            ]
        )
        lin_size = self._get_lin_size(layers)
        layers.extend(
            nn.ModuleList(
                [
                    nn.Linear(lin_size, n_linear),
                    # nn.Linear(n_linear, n_linear/2),
                ]
            )
        )
        # Previous version: comment out this line in order to use previous state dicts
        self.feature_extraction = nn.Sequential(*layers)

        # layers = nn.ModuleList(
        #     [
        #         nn.Conv2d(input_size[0], 100, 3),
        #         nn.ReLU(),
        #         nn.MaxPool2d((2, 2)),
        #         nn.Dropout(dropout),
        #         nn.Conv2d(100, 100, 3),
        #         nn.MaxPool2d((2, 2)),
        #         nn.Dropout(dropout),
        #         nn.Conv2d(100, 300, (2, 3)),
        #         nn.MaxPool2d((2, 2)),
        #         nn.Dropout(dropout),
        #         nn.Conv2d(300, 300, (1, 7)),
        #         nn.MaxPool2d((2, 2)),
        #         nn.Dropout(dropout),
        #         nn.Conv2d(300, 100, (1, 3)),
        #         nn.Conv2d(100, 100, (1, 3)),
        #         Flatten(),
        #     ]
        # )

        # nn.Conv2d(input_size[0], n_channels, (input_size[1], 1)),
        # nn.BatchNorm2d(n_channels),
        # nn.ReLU(),
        # nn.Conv2d(n_channels, 2 * n_channels, (1, filter_size)),
        # nn.BatchNorm2d(2 * n_channels),
        # nn.ReLU(),
        # nn.MaxPool2d((1, 4)),
        # nn.Conv2d(2 * n_channels, 4 * n_channels, (1, int(filter_size / 2))),
        # nn.BatchNorm2d(4 * n_channels),
        # # nn.Dropout(dropout1),
        # nn.ReLU(),
        # nn.MaxPool2d((1, 4)),
        # nn.Conv2d(4 * n_channels, 8 * n_channels, (1, int(filter_size / 4))),
        # # nn.ReLU(),
        # # nn.MaxPool2d((1, 5)),
        # # nn.Conv2d(8 * n_channels, 16 * n_channels, (1, int(filter_size / 5))),
        # # nn.BatchNorm2d(16 * n_channels),
        # nn.BatchNorm2d(8 * n_channels),
        # nn.ReLU(),
        # Flatten(),

        # Previous version: unceomment this line and comment the next in order to use previous
        # state dicts Don't forget to remove unpacking (*)
        # layers.extend(
        self.classif = nn.Sequential(
            *nn.ModuleList(
                [
                    nn.Linear(n_linear, 2),
                    # nn.Linear(n_linear/2, 2),
                ]
            )
        )
        # Previous version: uncomment this line and comment out forward method in order to use
        # previous state dicts
        # self.model = nn.Sequential(*layers)

        # self.model = lambda x: self.classif(self.feature_extraction(x))

    def forward(self, x):
        return self.classif(self.feature_extraction(x))


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

    ###############
    ### PARSING ###
    ###############

    parser.add_argument(
        "-f", "--filters", default=8, type=int, help="The size of the first convolution"
    )

    args = parser.parse_args()
    data_path = args.path
    if not data_path.endswith("/"):
        data_path += "/"
    save_path = args.save
    if not save_path.endswith("/"):
        save_path += "/"
    data_type = args.feature
    batch_size = args.batch_size
    max_subj = args.max_subj
    ch_type = args.elec
    features = args.feature
    debug = args.debug
    chunkload = args.chunkload
    filters = args.filters
    nchan = args.nchan
    dropout = args.dropout
    dropout_option = args.dropout_option
    linear = args.linear
    seed = args.seed
    mode = args.mode
    train_size = args.train_size
    num_workers = args.num_workers
    model_name = args.model_name
    times = args.times
    patience = args.patience
    learning_rate = args.lr
    log = args.log
    printmem = args.printmem

    ####################
    ### Starting log ###
    ####################

    if log:
        logging.basicConfig(
            filename=save_path + model_name + ".log",
            filemode="a",
            level=logging.DEBUG,
            format="%(asctime)s %(message)s",
            datefmt="%m/%d/%Y %I:%M:%S %p",
        )
    else:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(message)s",
            datefmt="%m/%d/%Y %I:%M:%S %p",
        )

    ###########################
    ### Torchsummary checks ###
    ###########################

    torchsum = True
    try:
        from torchsummary import summary
    except:
        logging.warning("Warning: Error loading torchsummary")
        torchsum = False

    #####################
    ### Parser checks ###
    #####################

    if printmem and chunkload:
        logging.info(
            "Warning: chunkload and printmem selected, but chunkload does not allow for printing memory as it loads in chunks during training"
        )

    #########################
    ### CUDA verification ###
    #########################

    if torch.cuda.is_available():
        device = "cuda"
    else:
        logging.warning("Warning: gpu device not available")
        device = "cpu"

    ##################
    ### data types ###
    ##################

    if ch_type == "MAG":
        n_channels = 102
    elif ch_type == "GRAD":
        n_channels = 204
    elif ch_type == "ALL":
        n_channels = 306
    else:
        raise (f"Error: invalid channel type: {ch_type}")

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

    if debug:
        logging.debug("ENTERING DEBUG MODE")
        max_subj = 20
        dropout = 0.5
        dropout_option = "same"
        patience = 1

    #########################
    ### preparing network ###
    #########################

    input_size = (n_channels // 102, 102, trial_length)

    # net = vanPutNet("vanputnet_512linear_GRAD", input_size).to(device)
    net = FullNet(
        # f"{model_name}_{dropout_option}_dropout{dropout}_filter{filters}_nchan{n_channels}_lin{linear}",
        f"{model_name}_{ch_type}_dropout{dropout}_filter{filters}_nchan{nchan}_lin{linear}",
        input_size,
        filters,
        nchan,
        linear,
        dropout,
        dropout_option,
    ).to(device)

    if times:
        # overrides default mode !
        # tests different values of workers and batch sizes to check which is the fastest
        num_workers = [16, 32, 64, 128]
        batch_sizes = [16, 32]
        perfs = []
        for nw, bs in product(num_workers, batch_sizes):
            tl, vl, _ = create_loaders(
                data_path,
                train_size,
                bs,
                max_subj,
                ch_type,
                data_type,
                num_workers=nw,
                debug=debug,
                chunkload=chunkload,
            )
            tpb, et = train(net, tl, vl, "", lr=learning_rate, timing=True)
            perfs.append((nw, bs, tpb, et))

        for x in sorted(perfs, key=lambda x: x[-1]):
            logging.info(f"\n{x[0]} {x[1]} {nt(x[2])} {nt(x[3])}")

    else:
        if torchsum:
            logging.info(summary(net, input_size))
        else:
            logging.info(net)

        # We create loaders and datasets (see dataloaders.py)
        trainloader, validloader, testloader = create_loaders(
            data_path,
            train_size,
            batch_size,
            max_subj,
            ch_type,
            data_type,
            seed=seed,
            num_workers=num_workers,
            chunkload=chunkload,
            debug=debug,
            printmem=printmem,
        )

        if mode == "overwrite":
            save = True
            load = False
        elif mode in ("continue", "evaluate"):
            save = True
            load = True
        else:
            save = False
            load = False

        model_filepath = save_path + net.name + ".pt"
        logging.info(net.name)
        # Actual training (loading nework if existing and load option is True)
        if mode != "evaluate":
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
                mode=mode,
            )

        # Loading best saved model
        if os.path.exists(model_filepath):
            _, net_state, _ = load_checkpoint(model_filepath)
            net.load_state_dict(net_state)
        else:
            logging.warning(
                f"Error: Can't evaluate model {model_filepath}, file not found."
            )
            exit()

        # testing
        logging.info("Evaluating on valid set:")
        results = loadmat(model_filepath[:-2] + "mat")
        logging.info(
            f"loss: {results['loss_score']} // accuracy: {results['acc_score']}"
        )
        logging.info(f"best epoch: {results['best_epoch']}/{results['n_epochs']}")
        exit()

        # # Final testing
        # print("Evaluating on test set:")
        # tloss, tacc = evaluate(net, testloader)
        # print("loss: ", tloss, " // accuracy: ", tacc)
        # if save:
        #     results = loadmat(model_filepath[:-2] + "mat")
        #     print("best epoch: ", f"{results['best_epoch']}/{results['n_epochs']}")
        #     results["test_acc"] = tacc
        #     results["test_loss"] = tloss
        #     savemat(save_path + net.name + ".mat", results)
