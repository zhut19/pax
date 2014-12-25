import numpy as np
from pax import plugin, datastructure, dsputils, units

# Used for diagnostic plotting only
# TODO: factor out to separate plugin?
import matplotlib.pyplot as plt
import os

class FindSmallPeaks(plugin.TransformPlugin):

    def startup(self):

        # Get settings from configuration
        self.min_sigma = self.config['peak_minimum_sigma']
        self.initial_noise_sigma = self.config['noise_sigma_guess']

        # Optional settings
        self.filter_to_use = self.config.get('filter_to_use', None)
        self.give_up_after = self.config.get('give_up_after_peak_of_size', float('inf'))
        self.max_noise_detection_passes = self.config.get('max_noise_detection_passes', float('inf'))
        self.make_diagnostic_plots_in = self.config.get('make_diagnostic_plots_in', None)
        if self.make_diagnostic_plots_in is not None:
            if not os.path.exists(self.make_diagnostic_plots_in):
                os.makedirs(self.make_diagnostic_plots_in)

        self.channels_in_detector = {
            'tpc':  self.config['pmts_top'] | self.config['pmts_bottom'],
        }
        for det, chs in self.config['external_detectors'].items():
            self.channels_in_detector[det] = chs

    def transform_event(self, event):
        # ocs is shorthand for occurrences, as usual

        # Any ultra-large peaks after which we can give up?
        large_peak_start_points = [p.left for p in event.peaks if p.area > self.give_up_after]
        if len(large_peak_start_points) > 0:
            give_up_after = min(large_peak_start_points)
        else:
            give_up_after = float('inf')

        noise_count = {}
        event.bad_channels = []

        # Handle each detector separately
        for detector in self.channels_in_detector.keys():

            # Get all free regions before the give_up_after point
            for region_left, region_right in dsputils.free_regions(event, detector):

                # Can we give up yet?
                if region_left >= give_up_after:
                    break

                # Find all occurrences completely enveloped in the free region. Thank pyintervaltree for log(n) runtime
                # TODO: we should put strict=False, so we're not relying on the zero-suppression to separate
                # small peaks close to a large peak. However, right now this brings in stuff from large peaks if their
                # boundsare not completely tight...
                ocs = event.occurrences_interval_tree.search(region_left, region_right, strict=True)
                self.log.debug("Free region %05d-%05d: process %s occurrences" % (region_left, region_right, len(ocs)))

                for oc in ocs:
                    # Focus only on the part of the occurrence inside the free region (superfluous as long as strict=True)
                    # Remember: intervaltree uses half-open intervals, stop is the first index outside
                    start = max(region_left, oc.begin)
                    stop = min(region_right + 1, oc.end)
                    channel = oc.data['channel']

                    # Don't consider channels from other detectors
                    if channel not in self.channels_in_detector[detector]:
                        continue

                    # Maybe some channels have already been marked as bad (configuration?), don't consider these.
                    if channel in event.bad_channels:
                        continue

                    # Retrieve the waveform from pmt_waveforms
                    w = event.pmt_waveforms[channel, start:stop]

                    # Keep a copy, so we can filter w if needed:
                    origw = w

                    # Apply the filter, if user wants to
                    if self.filter_to_use is not None:
                        w = np.convolve(w, self.filter_to_use, 'same')

                    # Use three passes to separate noise / peaks, see description in .... TODO
                    noise_sigma = self.initial_noise_sigma
                    old_raw_peaks = []
                    pass_number = 0
                    while True:
                        # Determine the peaks based on the noise level
                        # Can't just use w > self.min_sigma * noise_sigma here, want to extend peak bounds to noise_sigma
                        raw_peaks = self.find_peaks(w, noise_sigma)

                        if pass_number != 0 and raw_peaks == old_raw_peaks:
                            # No change in peakfinding, previous noise level is still valid
                            # That means there's no point in repeating peak finding either, and we can just:
                            break
                            # This saves about 25% of runtime
                            # You can't break if you find no peaks on the first pass:
                            # maybe the estimated noise level was too high

                        # Correct the baseline -- BuildWaveforms can get it wrong if there is a pe in the starting samples
                        w -= w[self.samples_without_peaks(w, raw_peaks)].mean()

                        # Determine the new noise_sigma
                        noise_sigma = w[self.samples_without_peaks(w, raw_peaks)].std()

                        old_raw_peaks = raw_peaks
                        if pass_number >= self.max_noise_detection_passes:
                            self.log.warning((
                                "In occurrence %s-%s in channel %s, findSmallPeaks did not converge on peaks after %s" +
                                " iterations. This could indicate a baseline problem in this occurrence. " +
                                "Channel-based peakfinding in this occurrence may be less accurate.") % (
                                    start, stop, channel, pass_number))
                            break

                        pass_number += 1

                    # Update the noise occurrence count
                    if len(raw_peaks) == 0:
                        noise_count[channel] = noise_count.get(channel, 0) + 1

                    # Store the found peaks in the datastructure
                    peaks = []
                    for p in raw_peaks:
                        peaks.append(datastructure.ChannelPeak({
                            # TODO: store occurrence index -- occurrences needs to be a better datastructure first
                            'channel':             channel,
                            'left':                start + p[0],
                            'index_of_maximum':    start + p[1],
                            'right':               start + p[2],
                            # NB: area and max are computed in filtered waveform, because
                            # the sliding window filter will shift the peak shape a bit
                            'area':                np.sum(w[p[0]:p[2]+1]),
                            'height':              w[p[1]],
                            'noise_sigma':         noise_sigma,
                        }))
                    event.channel_peaks.extend(peaks)

                    # TODO: move to separate plugin?
                    if self.make_diagnostic_plots_in:
                        plt.figure()
                        if self.filter_to_use is None:
                            plt.plot(w, drawstyle='steps', label='data')
                        else:
                            plt.plot(w, drawstyle='steps', label='data (filtered)')
                            plt.plot(origw, drawstyle='steps', label='data (raw)')
                        for p in raw_peaks:
                            plt.axvspan(p[0]-1, p[2], color='red', alpha=0.5)
                        plt.plot(noise_sigma * np.ones(len(w)), '--', label='1 sigma')
                        plt.plot(self.min_sigma * noise_sigma * np.ones(len(w)), '--', label='%s sigma' % self.min_sigma)
                        plt.legend()
                        bla = (event.event_number, start, stop, channel)
                        plt.title('Event %s, occurrence %d-%d, Channel %d' % bla)
                        plt.savefig(os.path.join(self.make_diagnostic_plots_in,  'event%04d_occ%05d-%05d_ch%03d.png' % bla))
                        plt.close()

        # Mark channels with an abnormally high noise rate as bad
        for ch, dc in noise_count.items():
            if dc > self.config['maximum_noise_occurrences_per_channel']:
                self.log.debug(
                    "Channel %s shows an abnormally high rate of noise pulses (%s): its spe pulses will be excluded" % (
                        ch, dc))
                event.bad_channels.append(ch)

        return event


    def find_peaks(self, w, noise_sigma):
        """
        Find all peaks at least self.min_sigma * noise_sigma above baseline.
        Peak boundaries are last samples above noise_sigma
        :param w: waveform to check for peaks
        :param noise_sigma: stdev of the noise
        :return: peaks as list of (left_index, max_index, right_index) tuples
        """
        peaks = []

        for left, right in dsputils.intervals_where(w > noise_sigma):
            max_idx = left + np.argmax(w[left:right + 1])
            height = w[max_idx]
            if height < noise_sigma * self.min_sigma:
                continue
            peaks.append((left, max_idx, right))
        return peaks

    def samples_without_peaks(self, w, peaks):
        """Return array of bools of same size as w, True if none of peaks live there"""
        not_in_peak = np.ones(len(w), dtype=np.bool)    # All True
        for p in peaks:
            not_in_peak[p[0]:p[2] + 1] = False
        return not_in_peak