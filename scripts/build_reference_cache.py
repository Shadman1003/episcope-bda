"""Package only attributable public inputs for a reproducible offline cache."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile


def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(1024*1024): result.update(block)
    return result.hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('data'))
    parser.add_argument('--output',type=Path,default=Path('resources/public-reference-cache.zip'))
    args=parser.parse_args()
    root=args.root.resolve()
    names=['districts.csv','districts.geojson','population.csv','weather.parquet','provenance.json']
    files=[root/'reference'/name for name in names]
    cache=root/'bronze/reference'
    files += [cache/name for name in ['districts_original.geojson','geoboundaries_metadata.json',
                                    'open_meteo_requests.json','worldpop_dataset.html']]
    for folder in ['worldpop','open_meteo']:
        files += [path for path in (cache/folder).rglob('*') if path.is_file() and path.suffix.lower() in ('.json','.tif')]
    if any(not path.is_file() or path.is_symlink() for path in files):
        raise ValueError('Public reference cache is incomplete or contains a symbolic link')
    records=[]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    temporary=args.output.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for path in sorted(files):
            relative=path.resolve().relative_to(root).as_posix()
            record={'path':relative,'bytes':path.stat().st_size,'sha256':digest(path)}
            records.append(record)
            info=zipfile.ZipInfo(relative,date_time=(1980,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,path.read_bytes(),compresslevel=9)
    if temporary.stat().st_size>=50_000_000:
        temporary.unlink()
        raise ValueError('Reference archive exceeds the repository data-file limit')
    temporary.replace(args.output)
    manifest={'created_utc':datetime.now(timezone.utc).isoformat(),
              'scope':'Public geography, modelled population and weather only; no synthetic facilities, encounters, stocks or secrets',
              'attribution':'See resources/README.md, generator/README.md and reference/provenance.json inside the archive',
              'archive_bytes':args.output.stat().st_size,'archive_sha256':digest(args.output),'files':records}
    args.output.with_suffix('.manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:manifest[key] for key in ['scope','archive_bytes','archive_sha256']}))


if __name__=='__main__':
    main()
