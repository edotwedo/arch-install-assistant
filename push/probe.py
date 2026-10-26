from archinstall.lib.disk.device_handler import device_handler
devs = device_handler.devices
print("TYPE", type(devs))
for d in devs:
    info = d.device_info
    print("DEV", info.path, info.total_size.value, info.total_size.unit, "sector", info.sector_size.value)
    for fr in info.free_space_regions:
        print("FREE start", fr.start, "end", fr.end, "sectors", fr.get_length())
    for p in d.partition_infos:
        print("PART", p.path, p.partn, p.type, p.fs_type, p.start.value, p.start.unit, p.length.value, p.length.unit, [f.description for f in p.flags], p.mountpoints)
