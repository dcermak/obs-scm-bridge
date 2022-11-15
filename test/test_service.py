from itertools import product

import xml.etree.ElementTree as ET

import pytest
from pytest_container import DerivedContainer
from pytest_container.container import ContainerData


_RPMS_DIR = "/src/rpms/"

_AAA_BASE_URL = "https://github.com/openSUSE/aaa_base"
_LIBECONF_URL = "https://github.com/openSUSE/libeconf"

CONTAINERFILE = f"""RUN zypper -n in python3 git build diff && \
    . /etc/os-release && [[ ${{NAME}} = "SLES" ]] || zypper -n in git-lfs

RUN git config --global user.name "SUSE Bot" && \
    git config --global user.email "noreply@suse.com" && \
    git config --global protocol.file.allow always

RUN mkdir -p {_RPMS_DIR}ring0 && \
    cd {_RPMS_DIR} && git clone {_LIBECONF_URL} && \
    cd libeconf && git rev-parse HEAD > /src/libeconf && \
    cd {_RPMS_DIR}ring0 && \
    git init && git submodule add {_AAA_BASE_URL} && \
    git commit -m "add aaa_base" && \
    git submodule add ../libeconf && git commit -m "add libeconf" && \
    cd aaa_base && git rev-parse HEAD > /src/aaa_base

COPY obs_scm_bridge /usr/bin/
"""

TUMBLEWEED = DerivedContainer(
    base="registry.opensuse.org/opensuse/tumbleweed", containerfile=CONTAINERFILE
)
LEAP_15_3, LEAP_15_4 = (
    DerivedContainer(
        base=f"registry.opensuse.org/opensuse/leap:15.{ver}",
        containerfile=CONTAINERFILE,
    )
    for ver in (3, 4)
)
BCI_BASE_15_3, BCI_BASE_15_4 = (
    DerivedContainer(
        base=f"registry.suse.com/bci/bci-base:15.{ver}", containerfile=CONTAINERFILE
    )
    for ver in (3, 4)
)


CONTAINER_IMAGES = [TUMBLEWEED, LEAP_15_3, LEAP_15_4, BCI_BASE_15_3, BCI_BASE_15_4]


_OBS_SCM_BRIDGE_CMD = "obs_scm_bridge --debug 1"


def test_service_help(auto_container: ContainerData):
    """This is just a simple smoke test to check whether the script works."""
    auto_container.connection.run_expect([0], "obs_scm_bridge --help")


def test_clones_the_repository(auto_container_per_test: ContainerData):
    """Check that the service clones the manually created repository correctly."""
    dest = "/tmp/ring0"
    auto_container_per_test.connection.run_expect(
        [0], f"obs_scm_bridge --outdir {dest} --url {_RPMS_DIR}ring0"
    )
    auto_container_per_test.connection.run_expect([0], f"diff {dest} {_RPMS_DIR}ring0")


def test_creates_packagelist(auto_container_per_test: ContainerData):
    """Smoke test for the generation of the package list files `$pkg_name.xml`
    and `$pkg_name.info`:

    - verify that the destination folder contains all expected `.info` and
      `.xml` files
    - check the `scmsync` elements in the `.xml` files
    - check the HEAD hashes in the `.info` files
    """
    dest = "/tmp/ring0"
    auto_container_per_test.connection.run_expect(
        [0], f"obs_scm_bridge --outdir {dest} --url {_RPMS_DIR}ring0 --projectmode 1"
    )
    libeconf_hash, aaa_base_hash = (
        auto_container_per_test.connection.file(
            f"/src/{pkg_name}"
        ).content_string.strip()
        for pkg_name in ("libeconf", "aaa_base")
    )

    files = auto_container_per_test.connection.file(dest).listdir()
    assert len(files) == 4
    for file_name in (
        f"{pkg}.{ext}"
        for pkg, ext in product(("aaa_base", "libeconf"), ("xml", "info"))
    ):
        assert file_name in files

    def _test_pkg_xml(pkg_name: str, expected_url: str, expected_head_hash: str):
        conf = ET.fromstring(
            auto_container_per_test.connection.file(
                f"{dest}/{pkg_name}.xml"
            ).content_string
        )
        assert conf.attrib["name"] == pkg_name
        scm_sync_elements = conf.findall("scmsync")
        assert len(scm_sync_elements) == 1 and scm_sync_elements[0].text
        assert f"{expected_url}#{expected_head_hash}" in scm_sync_elements[0].text

    _test_pkg_xml("aaa_base", _AAA_BASE_URL, aaa_base_hash)
    _test_pkg_xml("libeconf", f"{_RPMS_DIR}libeconf", libeconf_hash)

    for pkg_name, pkg_head_hash in (
        ("aaa_base", aaa_base_hash),
        ("libeconf", libeconf_hash),
    ):
        assert (
            pkg_head_hash
            == auto_container_per_test.connection.file(
                f"{dest}/{pkg_name}.info"
            ).content_string.strip()
        )


LFS_REPO = "https://gitea.opensuse.org/adrianSuSE/git-example-lfs"


@pytest.mark.parametrize("fragment", ["", "#dc16ed074a49fbd104166d979b3045cc5d84db04"])
@pytest.mark.parametrize("query", ["", "?lfs=1"])
@pytest.mark.parametrize(
    "container_per_test", [TUMBLEWEED, LEAP_15_3, LEAP_15_4], indirect=True
)
def test_downloads_lfs(container_per_test: ContainerData, fragment: str, query: str):
    """Test that the lfs file is automatically downloaded from the lfs server on
    clone.

    """
    _DEST = "/tmp/lfs-example"
    container_per_test.connection.run_expect(
        [0], f"{_OBS_SCM_BRIDGE_CMD} --outdir {_DEST} --url {LFS_REPO}{query}{fragment}"
    )

    tar_archive = container_per_test.connection.file(f"{_DEST}/orangebox-0.2.0.tar.gz")
    assert tar_archive.exists and tar_archive.is_file
    assert tar_archive.size > 10 * 1024


@pytest.mark.parametrize("fragment", ["", "#dc16ed074a49fbd104166d979b3045cc5d84db04"])
def test_lfs_opt_out(auto_container_per_test: ContainerData, fragment: str):
    _DEST = "/tmp/lfs-example"
    auto_container_per_test.connection.run_expect(
        [0], f"{_OBS_SCM_BRIDGE_CMD} --outdir {_DEST} --url {LFS_REPO}?lfs=0{fragment}"
    )

    tar_archive = auto_container_per_test.connection.file(
        f"{_DEST}/orangebox-0.2.0.tar.gz"
    )
    assert tar_archive.exists and tar_archive.is_file
    assert tar_archive.size < 1024
    assert "version https://git-lfs.github.com/spec" in tar_archive.content_string
