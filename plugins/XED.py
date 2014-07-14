import numpy as np

from pax import plugin, units
import math,sys,bz2
import io


class Xed(plugin.InputPlugin):

    file_header = np.dtype([
        ("dataset_name",            "S64"),
        ("creation_time",           "<u4"),
        ("first_event_number",      "<u4"),
        ("events_in_file",          "<u4"),
        ("event_index_size",        "<u4")
    ])

    event_header = np.dtype([
        ("dataset_name",            "S64"),
        ("utc_time",                "<u4"),
        ("utc_time_usec",           "<u4"),
        ("event_number",            "<u4"),
        ("chunks",                  "<u4"),
        #This is where the 'chunk layer' starts... but there always seems to be one chunk per event
        #I'll always assume this is true and raise an exception otherwise
        ("type",                    "S4"),
        ("size",                    "<u4"),
        ("sample_precision",        "<i2"),
        ("flags",                   "<u2"), #indicating compression type.. I'll assume bzip2 always
        ("samples_in_event",        "<u4"),
        ("voltage_range",           "<f4"),
        ("sampling_frequency",      "<f4"),
        ("channels",                "<u4"),
    ])

    def __init__(self, config):
        self.input = open('test.xed','rb')
        file_metadata      =  np.fromfile(self.input, dtype=Xed.file_header, count=1)[0]
        #print "File data: " + str(file_metadata)
        assert file_metadata['events_in_file'] == file_metadata['event_index_size']
        self.event_positions = np.fromfile(self.input, dtype=np.dtype("<u4"), count=file_metadata['event_index_size'])
        
    #This spends a lot of time growing the numpy array. Maybe faster if we first allocate 40000 zeroes.
    def GetEvents(self):
        input = self.input
        for event_position in self.event_positions:
            self.current_event_channels = {}
            if not input.tell() == event_position:
                raise ValueError("Reading error: this event should be at %s, but we are at %s!" % (event_position, input.tell()))
            self.event_metadata = np.fromfile(input, dtype=Xed.event_header, count=1)[0]
            #print "Reading event %s, consisting of %s chunks" % (self.event_metadata['event_number'], self.event_metadata['chunks'])
            if self.event_metadata['chunks'] != 1:
                raise NotImplementedError("The day has come: event with %s chunks found!" % event_metadata['chunks'])
            #print "Event type %s, size %s, samples %s, channels %s" % (self.event_metadata['type'], self.event_metadata['size'], self.event_metadata['samples_in_chunk'], event_metadata['channels'],)
            if self.event_metadata['type'] == b'zle0':
                event = {
                    'channel_occurences'    :   {},
                    'length'            :   self.event_metadata['samples_in_event']
                }
                """
                Read the arcane mask
                Hope this is ok with endianness and so on... no idea... what happens if it is wrong??
                TODO: Check with the actual bits in a hex editor..
                """
                mask_bytes = 4 * int(math.ceil(self.event_metadata['channels']/32))
                mask = self.bytestobits(
                    np.fromfile(input, dtype=np.dtype('<S%s'% mask_bytes), count=1)[0]
                )   #Last bytes are on front or something? Maybe whole mask is a single little-endian field??
                channels_included = [i for i,m in enumerate(reversed(mask)) if m == 1]
                chunk_fake_file = io.BytesIO(bz2.decompress(input.read(self.event_metadata['size']-28-mask_bytes)))  #28 is the chunk header size. TODO: only decompress if needed
                for channel_id in channels_included:
                    event['channel_occurences'][channel_id] = []
                    channel_waveform = np.array([],dtype="<i2")
                    #Read channel size (in 4bit words), subtract header size, convert from 4-byte words to bytes
                    channel_data_size =  int(4*(np.fromstring(chunk_fake_file.read(4), dtype='<u4')[0] - 1))
                    #print "Data size for channel %s is %s bytes" % (channel_id, channel_data_size)
                    channel_fake_file = io.BytesIO(chunk_fake_file.read(channel_data_size))
                    sample_position = 0
                    while 1:
                        control_word_string = channel_fake_file.read(4)
                        if not control_word_string: break
                        control_word = int(np.fromstring(control_word_string, dtype='<u4')[0])
                        if control_word<2**31:
                            #print "Next %s samples are 0" % (2*control_word)
                            sample_position += 2*control_word
                            #channel_waveform = np.append(channel_waveform, np.zeros(2*control_word))
                            continue
                        else:
                            data_samples = 2*(control_word-(2**31))
                            #print "Now reading %s data samples" % data_samples
                            samples_occurence = np.fromstring(channel_fake_file.read(2*data_samples), dtype="<i2")
                            event['channel_occurences'][channel_id].append((
                                sample_position,
                                samples_occurence
                            ))
                            sample_position += len(samples_occurence)
                            """
                            According to Guillaume's thesis, and the FADC manual, samples come in pairs, with later sample first!
                            This would mean we have to ungarble them (split into pairs, reverse the pairs, join again): 
                                channel_waveform = np.append(channel_waveform, ungarble_samplepairs(samples_fromfile-baseline))
                            However, this makes the peak come out two-headed... Maybe they were already un-garbled by some previous program??
                            We won't do any ungarbling for now:
                            """
            else:
                raise NotImplementedError("Still have to code grokking for sample type %s..." % self.event_metadata['type'])
            yield event
            
        #If we get here, all events have been read
        self.input.close()
            
    def bytestobits(self,bytes):
        bits = []
        if sys.version_info < (3, 0):
            bytes = (ord(bs) for bs in bytes_string)
        for b in bytes:
            for i in range(8):
                bits.append( (b >> i) & 1 )
        return bits