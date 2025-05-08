import logging
import os
import shutil

from git import Repo


class RepositoryReader:
    """
    Klonuje Git repozitár, načíta všetky .py súbory a odstráni lokálnu kópiu.
    """

    def __init__(self, repo_url: str, clone_dir: str = "./cloned_repo"):
        self.repo_url = repo_url
        self.clone_dir = clone_dir
        self.local_path = os.path.abspath(clone_dir)

    def clone_repository(self):
        """
        Naklonuje repozitár lokálne.
        """
        if not os.path.exists(self.clone_dir):
            print(f"Cloning repository from {self.repo_url} into {self.clone_dir}...")
            try:
                Repo.clone_from(self.repo_url, self.clone_dir)
            except Exception as e:
                logging.error(f"Klonovanie zlyhalo.")
                raise RuntimeError("Nepodarilo sa naklonovať repozitár. Skontrolujte prosím URL.") from e
        else:
            raise RuntimeError(f"Repository {self.clone_dir} is not empty.")

    def read_files(self):
        """
        Prečíta všetky .py súbory z repozitára.
        """
        files_dict = {}
        for root, _, files in os.walk(self.clone_dir):
            for file in files:
                if file.endswith(".py"):
                    file_path = os.path.join(root, file)
                    with open(file_path, "r", encoding="utf-8") as f:
                        files_dict[file_path] = f.read()
        return files_dict

    def delete_repository(self):
        """
        Vymaže repozitár
        """
        if os.path.exists(self.clone_dir):
            try:
                shutil.rmtree(self.clone_dir)
            except Exception as e:
                logging.error("Vymazanie priečinka zlyhalo.", exc_info=True)
                raise RuntimeError(f"Nepodarilo sa vymazať priečinok '{self.clone_dir}'.") from e
