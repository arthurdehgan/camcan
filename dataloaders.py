from __future__ import print_function, division
from time import time
import gc
import os
import torch
import psutil
import logging
import pandas as pd
import numpy as np
from scipy.stats import zscore
from scipy.signal import welch
from torch.utils.data import Dataset, DataLoader, random_split, TensorDataset
from params import NBINS


def extract_bands(data):
    if len(data.shape) < 3:
        data = data[np.newaxis, :, :]
        add_axis = True
    f = np.asarray([float(i / 2) for i in range(data.shape[-1])])
    # data = data[:, :, (f >= 8) * (f <= 12)].mean(axis=2)
    data = [
        data[:, :, (f >= 0.5) * (f <= 4)].mean(axis=-1)[..., None],
        data[:, :, (f >= 4) * (f <= 8)].mean(axis=-1)[..., None],
        data[:, :, (f >= 8) * (f <= 12)].mean(axis=-1)[..., None],
        data[:, :, (f >= 12) * (f <= 30)].mean(axis=-1)[..., None],
        data[:, :, (f >= 30) * (f <= 120)].mean(axis=-1)[..., None],
    ]
    data = np.concatenate(data, axis=2)
    if add_axis:
        return data[0]
    return data


def create_dataset(data_df, data_path, ch_type, dtype="temporal", debug=False):
    if ch_type == "MAG":
        chan_index = [2]
    elif ch_type == "GRAD":
        chan_index = [0, 1]
    elif ch_type == "ALL":
        chan_index = [0, 1, 2]

    meg_dataset = chunkedMegDataset(
        data_df=data_df,
        root_dir=data_path,
        chan_index=chan_index,
        dtype=dtype,
    )
    # else:
    #     sexlist = []
    #     data = None
    #     print("Loading data...")
    #     for row in data_df.iterrows():
    #         sub, sex, begin, end = row[1]
    #         f = f"{sub}_{sex}_{begin}_{end}_ICA_ds200.npy"
    #         file_path = os.path.join(data_path, f)
    #         trial = zscore(np.load(file_path)[chan_index], axis=1)
    #         data = (
    #             trial[np.newaxis, ...]
    #             if data is None
    #             else np.concatenate((trial[np.newaxis, ...], data))
    #         )
    #         sex = int(f.split("_")[1])
    #         sexlist.append(sex)

    #     if np.isnan(np.sum(trial)):
    #         print(file_path, "becomes nan")
    #     print("Data sucessfully loaded")

    #     meg_dataset = TensorDataset(torch.Tensor(data), torch.Tensor(sexlist))

    return meg_dataset


def create_loaders(
    data_folder,
    train_size,
    batch_size,
    max_subj,
    ch_type,
    dtype,
    debug=False,
    seed=0,
    num_workers=0,
    chunkload=True,
    printmem=False,
    include=(1, 1, 1),
    age=(0, 100),
):
    """create dataloaders iterators.

    include allows to only take one of the three outputs without loading data for the other loaders.
    by default include=(1,1,1) will load data for train, valid and test. if set to (0,1,0) It will
    only load data for the validation set and will return None for the others.
    """
    rng = np.random.RandomState(seed)
    torch.manual_seed(seed)
    # Using trials_df ensures we use the correct subjects that do not give errors since
    # it is created by reading the data. It is therefore better than SUB_DF previously used
    # We now use trials_df_clean that contains one less subjects that contained nans
    samples_df = pd.read_csv(f"{data_folder}trials_df_clean.csv", index_col=0)
    ages_df = (
        pd.read_csv(f"{data_folder}clean_participant_new.csv", index_col=0)
        .rename(columns={"participant_id": "subs"})
        .drop(["hand", "sex_text", "sex"], axis=1)
    )
    subs = (
        samples_df.drop(["begin", "end", "sex"], axis=1)
        .drop_duplicates(subset=["subs"])
        .reset_index(drop=True)
    )

    subs = subs.merge(ages_df[ages_df["subs"].isin(subs["subs"])].dropna(), "left")
    subs = np.array(subs[subs["age"].between(*age)].drop(["age"], axis=1).subs)
    idx = rng.permutation(range(len(subs)))
    subs = subs[idx]
    subs = subs[:max_subj]
    N = len(subs)
    train_size = int(N * train_size)

    remaining_size = N - train_size
    valid_size = int(remaining_size / 2)
    test_size = remaining_size - valid_size
    indexes = random_split(np.arange(N), [train_size, valid_size, test_size])
    logging.info(
        f"Using {N} subjects: {train_size} for train, {valid_size} for validation, and {test_size} for test"
    )

    bands = False
    load_fn = load_freq_data
    if dtype == "temporal":
        load_fn = load_data
    elif dtype == "bands":
        bands = True
    elif dtype == "both":
        load_fn = load_data

    dataframes = [
        samples_df.loc[samples_df["subs"].isin(subs[index])]
        .sample(frac=1, random_state=seed)
        .reset_index(drop=True)
        if include[i] == 1
        else None
        for i, index in enumerate(indexes)
    ]

    pin_memory = False
    if chunkload:
        pin_memory = True
        datasets = [
            create_dataset(
                df,
                data_folder,
                ch_type,
                dtype=dtype,
                debug=debug,
            )
            if include[i] == 1
            else None
            for i, df in enumerate(dataframes)
        ]

    else:
        logging.info("Loading Train Set")
        datasets = [
            TensorDataset(
                *load_fn(
                    df,
                    dpath=data_folder,
                    ch_type=ch_type,
                    bands=bands,
                    dtype=dtype,
                    debug=debug,
                    printmem=printmem,
                )
            )
            if include[i] == 1
            else None
            for i, df in enumerate(dataframes)
        ]

    # loading data with num_workers=0 is faster that using more because of IO read speeds on my machine.
    loaders = [
        DataLoader(
            st,
            batch_size=batch_size,
            num_workers=num_workers,
            pin_memory=pin_memory,
        )
        if include[i] == 1
        else None
        for i, st in enumerate(datasets)
    ]

    return loaders


class chunkedMegDataset(Dataset):
    """MEG dataset, from examples of the pytorch website: FaceLandmarks"""

    def __init__(self, data_df, root_dir, chan_index, dtype="temporal"):
        """
        Args:
            csv_file (string): Path to the csv file with annotations.
            root_dir (string): Directory with all the cut samples of MEG trials.
            chan_index (list): The index of electrodes to keep.
        """
        self.data_df = data_df
        self.root_dir = root_dir
        self.chan_index = chan_index
        self.dtype = dtype

    def __len__(self):
        return len(self.data_df)

    def __getitem__(self, idx):
        if torch.is_tensor(idx):
            idx = idx.tolist()

        sub = self.data_df["subs"].iloc[idx]
        sex = self.data_df["sex"].iloc[idx]
        begin = self.data_df["begin"].iloc[idx]
        end = self.data_df["end"].iloc[idx]

        if self.dtype == "temporal":
            data_path = os.path.join(
                self.root_dir, f"{sub}_{sex}_{begin}_{end}_ICA_ds200.npy"
            )
            trial = np.load(data_path)[self.chan_index]
            trial = zscore(trial, axis=1)
            if np.isnan(np.sum(trial)):
                logging.warning(f"Warning: {data_path} becomes nan")
        elif self.dtype.startswith("freq"):
            data_path = os.path.join(self.root_dir, f"{sub}_psd.npy")
            trial = np.load(data_path)[:, self.chan_index]
            if self.dtype == "freqbands":
                trial = extract_bands(trial)
        elif self.dtype == "both":
            data_path = os.path.join(
                self.root_dir, f"{sub}_{sex}_{begin}_{end}_ICA_ds200.npy"
            )
            signal = np.load(data_path)[self.chan_index]
            time = zscore(signal, axis=1)
            freq = np.zeros(list(signal.shape[:-1]) + [NBINS])
            for i, mat in enumerate(signal):
                for j, seg in enumerate(mat):
                    freq[i, j] = welch(seg, fs=200)[1]
            trial = np.concatenate((time, freq), axis=-1)

        sample = (trial, sex)

        return sample


def load_freq_data(dataframe, dpath, ch_type="MAG", bands=True, debug=False):
    """Loading psd values, subject by subject. Still viable, takes some time
    but data is small, so not too much. Might need repairing as code has changed
    a lot since last time this function was used.
    """
    if ch_type == "MAG":
        chan_index = [2]
    elif ch_type == "GRAD":
        chan_index = [0, 1]
    elif ch_type == "ALL":
        chan_index = [0, 1, 2]

    if debug:
        # Not currently working
        print("ENTERING DEBUG MODE")
        nb = 5 if bands else 241
        dummy = np.zeros((25000, len(elec_index), nb))
        return torch.Tensor(dummy).float(), torch.Tensor(dummy).float()

    X = None
    y = []
    i = 0
    for row in dataframe.iterrows():
        print(f"loading subject {i+1}...")
        sub, lab = row[1]["participant_id"], row[1]["sex"]
        try:
            sub_data = np.array(np.load(dpath + f"{sub}_psd.npy"))[:, elec_index]
        except:
            print("There was a problem loading subject ", sub)

        X = sub_data if X is None else np.concatenate((X, sub_data), axis=0)
        y += [lab] * len(sub_data)
        i += 1
    if bands:
        X = extract_bands(X)
    return torch.Tensor(X).float(), torch.Tensor(y).long()


def load_data(
    dataframe,
    dpath,
    offset=2000,
    ch_type="MAG",
    bands=True,
    dtype="temporal",
    debug=False,
    printmem=False,
):
    """Loading data subject per subject.

    bands is here only for compatibility with load_freq_data"""
    if ch_type == "MAG":
        chan_index = [2]
    elif ch_type == "GRAD":
        chan_index = [0, 1]
    elif ch_type == "ALL":
        chan_index = [0, 1, 2]

    subs_df = (
        dataframe.drop(["begin", "end"], axis=1)
        .drop_duplicates(subset=["subs"])
        .reset_index(drop=True)
    )

    n_subj = len(subs_df)
    X = np.empty(n_subj, dtype=object)
    y = []
    logging.debug(f"Loading {n_subj} subjects data")
    if printmem:
        subj_sizes = []
        totmem = psutil.virtual_memory().total / 10 ** 9
        logging.info(f"Total Available memory: {totmem:.3f} Go")
    for i, row in enumerate(subs_df.iterrows()):
        if printmem:
            usedmem = psutil.virtual_memory().used / 10 ** 9
            memstate = f"Used memory: {usedmem:.3f} / {totmem:.3f} Go."
            if n_subj > 10:
                if i % (n_subj // 10) == 0:
                    logging.debug(memstate)
            else:
                logging.debug(memstate)

        sub, lab = row[1]["subs"], row[1]["sex"]
        try:
            sub_data = np.load(dpath + f"{sub}_ICA_transdef_mfds200.npy")[chan_index]
        except:
            logging.warning(f"Warning: There was a problem loading subject {sub}")
            continue

        sub_segments = dataframe.loc[dataframe["subs"] == sub].drop(["sex"], axis=1)
        if dtype == "both":
            sub_data = [
                np.append(
                    zscore(sub_data[:, :, begin:end], axis=1),
                    welch(sub_data, fs=200)[1],
                )
                for begin, end in zip(sub_segments["begin"], sub_segments["end"])
            ]
        elif dtype == "temporal":
            sub_data = [
                zscore(sub_data[:, :, begin:end], axis=1)
                for begin, end in zip(sub_segments["begin"], sub_segments["end"])
            ]

        sub_data = np.array(sub_data)
        X[i] = sub_data
        y += [lab] * len(sub_data)
    logging.info("Loading successfull\n")
    return torch.Tensor(np.concatenate(X, axis=0)), torch.Tensor(y)


def load_subject(sub, data_path, data=None, timepoints=500, ch_type="all"):
    """Loads a single subject from info found in the csv file. Deprecated, takes too much time.
    path needs to be updated as CHAN_DF is no longer defined and paths have changed.
    """
    df = pd.read_csv("{}/cleansub_data_camcan_participant_data.csv".format(data_path))
    df = df.set_index("participant_id")
    sex = (df["sex"])[sub]
    subject_file = "{}_rest.mat".format(data_path + sub)
    # trial = read_raw_fif(subject_file,
    #                      preload=True).pick_types(meg=True)[:][0]
    trial = np.load(subject_file)
    if ch_type == "all":
        mask = [True for _ in range(len(trial))]
        n_channels = 306
    elif ch_type == "mag":
        mask = CHAN_DF["mag_mask"]
        n_channels = 102
    elif ch_type == "grad":
        mask = CHAN_DF["grad_mask"]
        n_channels = 204
    else:
        raise ("Error : bad channel type selected")
    trial = trial[mask]

    n_trials = trial.shape[-1] // timepoints
    for i in range(1, n_trials - 1):
        curr = trial[:, i * timepoints : (i + 1) * timepoints]
        curr = curr.reshape(1, n_channels, timepoints)
        data = curr if data is None else np.concatenate((data, curr))
    labels = [sex] * (n_trials - 2)
    data = data.astype(np.float32, copy=False)
    return data, labels
