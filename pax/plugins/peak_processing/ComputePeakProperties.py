import numpy as np

from pax import plugin, utils


class BasicProperties(plugin.TransformPlugin):
    """Computes basic peak properies such as area and hit time spread ("width")
    """

    def startup(self):
        self.last_top_ch = np.max(np.array(self.config['channels_top']))

    def transform_event(self, event):

        for peak in event.peaks:
            # Compute the area per pmt and saturation count
            peak.n_saturated_per_channel = np.zeros(event.n_channels, dtype=np.int16)
            for s in peak.hits:
                ch = s.channel
                peak.area_per_channel[ch] += s.area
                peak.n_saturated_per_channel[ch] += s.n_saturated

            # Compute the total area and saturation count
            peak.area = np.sum(peak.area_per_channel)
            peak.n_saturated = np.sum(peak.n_saturated_per_channel)

            # Compute top fraction
            peak.area_fraction_top = np.sum(peak.area_per_channel[:self.last_top_ch + 1]) / peak.area

            # Compute timing quantities
            times = [s.center for s in peak.hits]
            peak.hit_time_mean, peak.hit_time_std = utils.weighted_mean_variance(times,
                                                                                 [s.area for s in peak.hits])
            peak.hit_time_std **= 0.5  # Convert variance to std

            # Compute mean amplitude / noise
            if event.event_number == 198:
                pass
            hit_areas = [hit.area for hit in peak.hits]
            for hit in peak.hits:
                if hit.noise_sigma == 0:
                    pass
            try:
                peak.mean_amplitude_to_noise = np.average([hit.height / hit.noise_sigma for hit in peak.hits],
                                                          weights=hit_areas)
            except ZeroDivisionError:
                pass

            # Compute central ranges
            dt = event.sample_duration
            leftmost = float('inf')
            rightmost = float('-inf')
            area_so_far = 0
            for hit in sorted(peak.hits, key=lambda hit: abs(hit.center - peak.hit_time_mean)):
                if hit.left < leftmost:
                    leftmost = hit.left
                if hit.right > rightmost:
                    rightmost = hit.right
                area_so_far += hit.area
                if peak.range_50p_area == 0:
                    if area_so_far >= 0.5 * peak.area:
                        peak.range_50p_area = (rightmost - leftmost + 1) * dt
                if area_so_far >= 0.9 * peak.area:
                    peak.range_90p_area = (rightmost - leftmost + 1) * dt
                    break

        return event


class SumWaveformProperties(plugin.TransformPlugin):
    """Computes properties based on the hits-only sum waveform"""

    def startup(self):
        self.wv_field_len = int(self.config['peak_waveform_length'] / self.config['sample_duration']) + 1
        if not self.wv_field_len % 2:
            raise ValueError('peak_waveform_length must be an even multiple of the sample size')

    def transform_event(self, event):
        field_length = self.wv_field_len
        for peak in event.peaks:
            peak.sum_waveform = np.zeros(field_length, dtype=peak.sum_waveform.dtype)

            # Get the waveform and compute some properties
            w = event.get_sum_waveform(peak.detector).samples[peak.left:peak.right + 1]
            peak.center_time = (peak.left + np.average(np.arange(len(w)), weights=w)) * event.sample_duration
            cog_idx = int(round(peak.center_time / event.sample_duration)) - peak.left
            max_idx = np.argmax(w)
            peak.index_of_maximum = peak.left + max_idx
            peak.height = w[max_idx]

            self.log.debug("Waveform length %s samples, center at the %sth sample" % (len(w), cog_idx))

            # Chop the peak's waveform to the right length, then store it
            field_center = int(field_length/2) + 1
            left_overhang = cog_idx - field_center
            if left_overhang > 0:
                # Chop off the left overhang
                self.log.debug("Left overhang of %d samples" % left_overhang)
                w = w[left_overhang:]
                cog_idx = field_center
            right_overhang = len(w) - field_length + (field_center - cog_idx)
            if right_overhang > 0:
                self.log.debug("Right overhang of %d samples" % right_overhang)
                # Chop off any remaining right overhang
                w = w[:len(w)-right_overhang]

            start_idx = field_center - cog_idx
            self.log.debug("Indices in field: %s - %s" % (start_idx, start_idx + len(w)))
            peak.sum_waveform[start_idx:start_idx + len(w)] = w

        return event


class HitpatternSpread(plugin.TransformPlugin):
    """Computes the weighted root mean square deviation of the top and bottom hitpattern for each peak
    """

    def startup(self):

        # Grab PMT numbers and x, y locations in each array
        self.pmts = {}
        self.locations = {}
        for array in ('top', 'bottom'):
            self.pmts[array] = self.config['channels_%s' % array]
            self.locations[array] = {}
            for dim in ('x', 'y'):
                self.locations[array][dim] = np.array([self.config['pmt_locations'][ch][dim]
                                                       for ch in self.pmts[array]])

    def transform_event(self, event):

        for peak in event.peaks:

            # No point in computing this for veto peaks
            if peak.detector != 'tpc':
                continue

            for array in ('top', 'bottom'):

                hitpattern = peak.area_per_channel[self.pmts[array]]

                if np.all(hitpattern == 0.0):
                    # Empty hitpatterns will give error in np.average
                    continue

                weighted_var = 0
                for dim in ('x', 'y'):
                    _, wv = utils.weighted_mean_variance(self.locations[array][dim],
                                                         weights=hitpattern)
                    weighted_var += wv

                setattr(peak, '%s_hitpattern_spread' % array, np.sqrt(weighted_var))

        return event
