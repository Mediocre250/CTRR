import random
from enum import Enum
import torch
from torch.utils.data import Dataset


class Split(Enum):
    """ Defines whether to split for train or test.
    """
    TRAIN = 0
    TEST = 1


class Usage(Enum):
    """
    Each user has a different amount of sequences. The usage defines
    how many sequences are used:

    MAX: each sequence of any user is used (default)
    MIN: only as many as the minimal user has
    CUSTOM: up to a fixed amount if available.

    The unused sequences are discarded. This setting applies after the train/test split.
    """

    MIN_SEQ_LENGTH = 0
    MAX_SEQ_LENGTH = 1
    CUSTOM = 2


class PoiDataset(Dataset):
    """
    Our Point-of-interest pytorch dataset: To maximize GPU workload we organize the data in batches of
    "user" x "a fixed length sequence of locations". The active users have at least one sequence in the batch.
    In order to fill the batch all the time we wrap around the available users: if an active user
    runs out of locations we replace him with a new one. When there are no unused users available
    we reuse already processed ones. This happens if a single user was way more active than the average user.
    The batch guarantees that each sequence of each user was processed at least once.

    This data management has the implication that some sequences might be processed twice (or more) per epoch.
    During training you should call PoiDataset::shuffle_users before the start of a new epoch. This
    leads to more stochastic as different sequences will be processed twice.
    During testing you *have to* keep track of the already processed users.

    Working with a fixed sequence length omits awkward code by removing only few of the latest checkins per user. We
    work with a 80/20 train/test spilt, where test check-ins are strictly after training checkins. To obtain at least
    one test sequence with label we require any user to have at least (5*<sequence-length>+1) checkins in total.
    """

    def reset(self):
        # reset training state:
        self.next_user_idx = 0  # current user index to add
        self.active_users = []  # current active users
        self.active_user_seq = []  # current active users sequences
        self.user_permutation = []  # shuffle users during training

        # set active users:
        for i in range(self.batch_size):  # 200 or 1024
            self.next_user_idx = (self.next_user_idx + 1) % len(self.users)  # 200 or 1024
            self.active_users.append(i)  # [0, 1, ..., 199] or [0, 1, ..., 1023]
            self.active_user_seq.append(0)  # [0, 0, ..., 0] start from user's first subsequence

        # use 1:1 permutation:
        for i in range(len(self.users)):
            self.user_permutation.append(i)  # [0, 1, ..., User_NUM-1]

    def shuffle_users(self):
        random.shuffle(self.user_permutation)  # ??????????????????
        # reset active users:
        self.next_user_idx = 0
        self.active_users = []
        self.active_user_seq = []
        for i in range(self.batch_size):
            self.next_user_idx = (self.next_user_idx + 1) % len(self.users)  # 200(1024),???????????????????????????????????????201?????????
            self.active_users.append(self.user_permutation[i])  # ??????????????????????????????200???(1024)??????
            self.active_user_seq.append(0)

    def __init__(self, users, times, time_slots, coords, locs, regions, sequence_length, batch_size, split, usage, loc_count,
                 custom_seq_count):
        self.users = users
        self.locs = locs
        self.regions = regions
        self.times = times
        self.time_slots = time_slots
        self.coords = coords

        self.labels = []  # ????????????
        self.lbl_times = []  # labels????????????GPS????????????timestamp
        self.lbl_time_slots = []
        self.lbl_coords = []
        self.lbl_regions = []

        self.sequences = []  # ????????????,?????????????????????????????????(20)??????????????????????????????????????????check-ins
        self.sequences_times = []
        self.sequences_time_slots = []
        self.sequences_coords = []
        self.sequences_regions = []

        self.sequences_labels = []
        self.sequences_lbl_times = []
        self.sequences_lbl_time_slots = []
        self.sequences_lbl_coords = []
        self.sequences_lbl_regions = []

        self.sequences_count = []  # ???????????????????????????????????????
        self.Ps = []  # ???????
        self.Qs = torch.zeros(loc_count, 1)  # ?????????????
        self.usage = usage  # MAX_SEQ_LENGTH
        self.batch_size = batch_size  # 200 or 1024
        self.loc_count = loc_count
        self.custom_seq_count = custom_seq_count  # 1

        self.reset()

        # collect locations:
        for i in range(loc_count):
            self.Qs[i, 0] = i

        # align labels to locations (shift by one)
        for i, loc in enumerate(locs):
            self.locs[i] = loc[:-1]  # data,??????
            self.labels.append(loc[1:])  # labels,??????
            self.lbl_times.append(self.times[i][1:])
            self.lbl_time_slots.append(self.time_slots[i][1:])
            self.lbl_coords.append(self.coords[i][1:])
            self.lbl_regions.append(self.regions[i][1:])

            self.times[i] = self.times[i][:-1]
            self.time_slots[i] = self.time_slots[i][:-1]
            self.coords[i] = self.coords[i][:-1]
            self.regions[i] = self.regions[i][:-1]

        # split to training / test phase:
        for i, (time, time_slot, coord, loc, region, label, lbl_time, lbl_time_slot, lbl_coord, lbl_region) in enumerate(zip(self.times, self.time_slots, self.coords, self.locs, self.regions, self.labels, self.lbl_times, self.lbl_time_slots, self.lbl_coords, self.lbl_regions)):
            train_thr = int(len(loc) * 0.8)
            if split == Split.TRAIN:
                self.locs[i] = loc[:train_thr]
                self.times[i] = time[:train_thr]
                self.time_slots[i] = time_slot[:train_thr]
                self.coords[i] = coord[:train_thr]
                self.regions[i] = region[:train_thr]

                self.labels[i] = label[:train_thr]
                self.lbl_times[i] = lbl_time[:train_thr]
                self.lbl_time_slots[i] = lbl_time_slot[:train_thr]
                self.lbl_coords[i] = lbl_coord[:train_thr]
                self.lbl_regions[i] = lbl_region[:train_thr]

            if split == Split.TEST:
                self.locs[i] = loc[train_thr:]
                self.times[i] = time[train_thr:]
                self.time_slots[i] = time_slot[train_thr:]
                self.coords[i] = coord[train_thr:]
                self.regions[i] = region[train_thr:]

                self.labels[i] = label[train_thr:]
                self.lbl_times[i] = lbl_time[train_thr:]
                self.lbl_time_slots[i] = lbl_time_slot[train_thr:]
                self.lbl_coords[i] = lbl_coord[train_thr:]
                self.lbl_regions[i] = lbl_region[train_thr:]

        # split location and labels to sequences:
        self.max_seq_count = 0
        self.min_seq_count = 10000000
        self.capacity = 0
        for i, (time, time_slot, coord, loc, region, label, lbl_time, lbl_time_slot, lbl_coord, lbl_region) in enumerate(
                zip(self.times, self.time_slots, self.coords, self.locs, self.regions, self.labels, self.lbl_times,
                    self.lbl_time_slots, self.lbl_coords, self.lbl_regions)):
            seq_count = len(loc) // sequence_length  # ?????????????????????sequence??????, ??????5???
            assert seq_count > 0, 'fix seq-length and min-checkins in order to have at least one test sequence in a 80/20 split!'
            seqs = []  # ????????????,?????????????????????????????????????????????????????????check-ins
            seq_times = []
            seq_time_slots = []
            seq_coords = []
            seq_regions = []

            seq_lbls = []
            seq_lbl_times = []
            seq_lbl_time_slots = []
            seq_lbl_coords = []
            seq_lbl_regions = []

            for j in range(seq_count):
                start = j * sequence_length
                end = (j + 1) * sequence_length
                seqs.append(loc[start:end])
                seq_times.append(time[start:end])
                seq_time_slots.append(time_slot[start:end])
                seq_coords.append(coord[start:end])
                seq_regions.append(region[start:end])

                seq_lbls.append(label[start:end])
                seq_lbl_times.append(lbl_time[start:end])
                seq_lbl_time_slots.append((lbl_time_slot[start:end]))
                seq_lbl_coords.append(lbl_coord[start:end])
                seq_lbl_regions.append(lbl_region[start:end])

            self.sequences.append(seqs)
            self.sequences_times.append(seq_times)
            self.sequences_time_slots.append(seq_time_slots)
            self.sequences_coords.append(seq_coords)
            self.sequences_regions.append(seq_regions)

            self.sequences_labels.append(seq_lbls)
            self.sequences_lbl_times.append(seq_lbl_times)
            self.sequences_lbl_time_slots.append(seq_lbl_time_slots)
            self.sequences_lbl_coords.append(seq_lbl_coords)
            self.sequences_lbl_regions.append(seq_lbl_regions)

            self.sequences_count.append(seq_count)
            self.capacity += seq_count
            self.max_seq_count = max(self.max_seq_count, seq_count)
            self.min_seq_count = min(self.min_seq_count, seq_count)

        # statistics
        if self.usage == Usage.MIN_SEQ_LENGTH:
            print(split, 'load', len(users), 'users with min_seq_count', self.min_seq_count, 'batches:', self.__len__())
        if self.usage == Usage.MAX_SEQ_LENGTH:
            print(split, 'load', len(users), 'users with max_seq_count', self.max_seq_count, 'batches:', self.__len__())
        if self.usage == Usage.CUSTOM:
            print(split, 'load', len(users), 'users with custom_seq_count', self.custom_seq_count, 'Batches:',
                  self.__len__())

    def sequences_by_user(self, idx):
        return self.sequences[idx]

    def __len__(self):
        """ Amount of available batches to process each sequence at least once.
        """

        if self.usage == Usage.MIN_SEQ_LENGTH:
            # min times amount_of_user_batches:
            return self.min_seq_count * (len(self.users) // self.batch_size)
        if self.usage == Usage.MAX_SEQ_LENGTH:
            # estimated capacity:
            estimated = self.capacity // self.batch_size
            return max(self.max_seq_count, estimated)
        if self.usage == Usage.CUSTOM:
            return self.custom_seq_count * (len(self.users) // self.batch_size)
        raise ValueError()

    def __getitem__(self, idx):
        """ Against pytorch convention, we directly build a full batch inside __getitem__.
        Use a batch_size of 1 in your pytorch data loader.

        A batch consists of a list of active users,
        their next location sequence with timestamps and coordinates.

        y is the target location and y_t, y_s the targets timestamp and coordinates. Provided for
        possible use.

        reset_h is a flag which indicates when a new user has been replacing a previous user in the
        batch. You should reset this users hidden state to initial value h_0.
        """

        seqs = []
        times = []
        time_slots = []
        coords = []
        regions = []

        lbls = []
        lbl_times = []
        lbl_time_slots = []
        lbl_coords = []
        lbl_regions = []

        reset_h = []
        for i in range(self.batch_size):
            i_user = self.active_users[i]  # [0, 1, ..., 199]
            j = self.active_user_seq[i]  # 0
            max_j = self.sequences_count[i_user]  # ??????i????????????????????????
            if self.usage == Usage.MIN_SEQ_LENGTH:
                max_j = self.min_seq_count
            if self.usage == Usage.CUSTOM:
                max_j = min(max_j, self.custom_seq_count)  # use either the users maxima count or limit by custom count
            if j >= max_j:  # ??????i?????????????????????????????????????????????
                # replace this user in current sequence:
                i_user = self.user_permutation[self.next_user_idx]  # ??????201??????????????????????????????
                j = 0
                self.active_users[i] = i_user
                self.active_user_seq[i] = j
                self.next_user_idx = (self.next_user_idx + 1) % len(self.users)
                while self.user_permutation[self.next_user_idx] in self.active_users:  # ?????????????????????????????????????????????
                    self.next_user_idx = (self.next_user_idx + 1) % len(self.users)
                # TODO: throw exception if wrapped around!
            # use this user:
            reset_h.append(j == 0)  # ??????????????????,???????????????h  True or False
            seqs.append(torch.tensor(self.sequences[i_user][j]))
            times.append(torch.tensor(self.sequences_times[i_user][j]))                                                                                                                                                                                                                                                                                                                                                                                
            time_slots.append(torch.tensor(self.sequences_time_slots[i_user][j]))
            coords.append(torch.tensor(self.sequences_coords[i_user][j]))
            regions.append(torch.tensor(self.sequences_regions[i_user][j]))

            lbls.append(torch.tensor(self.sequences_labels[i_user][j]))
            lbl_times.append(torch.tensor(self.sequences_lbl_times[i_user][j]))
            lbl_time_slots.append(torch.tensor(self.sequences_lbl_time_slots[i_user][j]))
            lbl_coords.append(torch.tensor(self.sequences_lbl_coords[i_user][j]))
            lbl_regions.append(torch.tensor(self.sequences_lbl_regions[i_user][j]))

            self.active_user_seq[i] += 1  # ????????????i?????????????????????????????????,j???1????????????i?????????????????????

        x = torch.stack(seqs, dim=1)
        t = torch.stack(times, dim=1)
        t_slot = torch.stack(time_slots, dim=1)
        s = torch.stack(coords, dim=1)
        r = torch.stack(regions, dim=1)

        y = torch.stack(lbls, dim=1)
        y_t = torch.stack(lbl_times, dim=1)
        y_t_slot = torch.stack(lbl_time_slots, dim=1)
        y_s = torch.stack(lbl_coords, dim=1)
        y_r = torch.stack(lbl_regions, dim=1)
        return x, t, t_slot, s, r, y, y_t, y_t_slot, y_s, y_r, reset_h, torch.tensor(self.active_users)
