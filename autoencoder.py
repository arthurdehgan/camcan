import os
import gc
import sys
import logging
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


class Flatten(nn.Module):
    # Flatten layer used to connect between feature extraction and classif parts of a net.
    def forward(self, x):
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
    criterion=nn.MSELoss(),
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
    # loss for each batch and each epoch. Everything is saved in a dictionnary
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
        best_vloss = results["loss_score"]
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
            X = X.view(X.size(0), -1)

            net.train()
            out = net.forward(X)
            loss = criterion(out, X)
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

        train_loss = evaluate(net, trainloader, criterion)
        valid_loss = evaluate(net, validloader, criterion)

        train_losses.append(train_loss)
        valid_losses.append(valid_loss)
        if valid_loss < best_vloss:
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
        if save_model:
            results = {
                "loss_score": [best_vloss],
                "valid_loss": valid_losses,
                "train_loss": train_losses,
                "best_epoch": best_epoch,
                "n_epochs": epoch,
                "patience": patience,
                "current_patience": j,
            }
            savemat(save_path + net.name + ".mat", results)

    return net


def evaluate(net, dataloader, criterion=nn.MSELoss()):
    # function to evaluate a network on a dataloader. will return loss
    net.eval()
    with torch.no_grad():
        LOSSES = 0
        ACCURACY = 0
        COUNTER = 0
        for batch in dataloader:
            X, y = batch
            y = y.view(-1).to(device)
            X = X.view(-1, *net.input_size).to(device)
            X = X.view(X.size(0), -1)

            out = net.forward(X)
            loss = criterion(out, X)
            n = y.size(0)
            LOSSES += loss.sum().data.cpu().numpy() * n
            COUNTER += n
        floss = LOSSES / float(COUNTER)
    return floss


class CustomNet(nn.Module):
    def __init__(self, model_name, input_size):
        super(CustomNet, self).__init__()
        self.input_size = input_size
        self.name = model_name
        logging.info(model_name)

    def save_model(self, filepath="."):
        if not filepath.endswith("/"):
            filepath += "/"

        orig_stdout = sys.stdout
        with open(filepath + self.name + ".txt", "a") as f:
            sys.stdout = f
            if torchsum:
                lin_size = input_size[0] * input_size[1] * input_size[2]
                summary(self, (1, lin_size))
            else:
                logging.info(self)
            sys.stdout = orig_stdout


class AutoEncoder(CustomNet):
    def __init__(
        self,
        model_name,
        input_size,
    ):
        super(AutoEncoder, self).__init__(model_name, input_size)

        lin_size = input_size[0] * input_size[1] * input_size[2]

        self.encoder = nn.Sequential(
            nn.Linear(lin_size, 1024),
            nn.ReLU(True),
            nn.Linear(1024, 512),
        )

        self.decoder = nn.Sequential(
            nn.Linear(512, 1024),
            nn.ReLU(True),
            nn.Linear(1024, lin_size),
            nn.Tanh(),
        )

    def forward(self, x):
        x = self.encoder(x)
        return self.decoder(x)


if __name__ == "__main__":

    ###############
    ### PARSING ###
    ###############

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

    nchan = 102
    if ch_type == "MAG":
        n_channels = 102
    elif ch_type == "GRAD":
        n_channels = 204
    elif ch_type == "ALL":
        n_channels = 306
    else:
        raise (f"Error: invalid channel type: {ch_type}")

    #########################
    ### preparing network ###
    #########################

    trial_length = TIME_TRIAL_LENGTH
    input_size = (n_channels // 102, nchan, trial_length)

    net = AutoEncoder(
        f"{model_name}_nchan{n_channels}",
        input_size,
    ).to(device)
    lin_size = input_size[0] * input_size[1] * input_size[2]

    if torchsum:
        logging.info(summary(net, (1, lin_size)))
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

    # evaluating
    logging.info("Evaluating on valid set:")
    results = loadmat(model_filepath[:-2] + "mat")
    logging.info(f"loss: {results['loss_score']}")
    logging.info(f"best epoch: {results['best_epoch']}/{results['n_epochs']}")
    exit()