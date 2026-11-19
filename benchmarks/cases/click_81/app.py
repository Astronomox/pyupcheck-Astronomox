import click

width, height = click.get_terminal_size()  # BREAK


@click.command()
@click.option("--name", default="world")
def main(name):
    click.echo(f"hello {name}")
